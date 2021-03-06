// Copyright (C) 2014  Open Data ("Open Data" refers to
// one or more of the following companies: Open Data Partners LLC,
// Open Data Research LLC, or Open Data Capital LLC.)
// 
// This file is part of Hadrian.
// 
// Licensed under the Hadrian Personal Use and Evaluation License (PUEL);
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
//     http://raw.githubusercontent.com/opendatagroup/hadrian/master/LICENSE
// 
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.opendatagroup.hadrian.lib1.model

import scala.language.postfixOps
import scala.collection.immutable.ListMap
import scala.util.Random

import org.apache.avro.AvroRuntimeException
import org.apache.avro.SchemaCompatibility.checkReaderWriterCompatibility
import org.apache.avro.SchemaCompatibility.SchemaCompatibilityType

import com.opendatagroup.hadrian.ast.LibFcn
import com.opendatagroup.hadrian.errors.PFARuntimeException
import com.opendatagroup.hadrian.jvmcompiler.JavaCode
import com.opendatagroup.hadrian.jvmcompiler.javaSchema

import com.opendatagroup.hadrian.ast.AstContext
import com.opendatagroup.hadrian.ast.ExpressionContext
import com.opendatagroup.hadrian.ast.FcnDef
import com.opendatagroup.hadrian.ast.FcnRef

import com.opendatagroup.hadrian.data.PFAArray
import com.opendatagroup.hadrian.data.PFARecord
import com.opendatagroup.hadrian.data.ComparisonOperator

import com.opendatagroup.hadrian.signature.P
import com.opendatagroup.hadrian.signature.Sig
import com.opendatagroup.hadrian.signature.Signature
import com.opendatagroup.hadrian.signature.Sigs

import com.opendatagroup.hadrian.datatype.Type
import com.opendatagroup.hadrian.datatype.FcnType
import com.opendatagroup.hadrian.datatype.AvroType
import com.opendatagroup.hadrian.datatype.AvroNull
import com.opendatagroup.hadrian.datatype.AvroBoolean
import com.opendatagroup.hadrian.datatype.AvroInt
import com.opendatagroup.hadrian.datatype.AvroLong
import com.opendatagroup.hadrian.datatype.AvroFloat
import com.opendatagroup.hadrian.datatype.AvroDouble
import com.opendatagroup.hadrian.datatype.AvroBytes
import com.opendatagroup.hadrian.datatype.AvroFixed
import com.opendatagroup.hadrian.datatype.AvroString
import com.opendatagroup.hadrian.datatype.AvroEnum
import com.opendatagroup.hadrian.datatype.AvroArray
import com.opendatagroup.hadrian.datatype.AvroMap
import com.opendatagroup.hadrian.datatype.AvroRecord
import com.opendatagroup.hadrian.datatype.AvroField
import com.opendatagroup.hadrian.datatype.AvroUnion

import com.opendatagroup.hadrian.lib1.array.argLowestN

package object cluster {
  private var fcns = Map[String, LibFcn]()
  def provides = fcns
  def provide(libFcn: LibFcn): Unit =
    fcns = fcns + Tuple2(libFcn.name, libFcn)

  val prefix = "model.cluster."

  //////////////////////////////////////////////////////////////////// 

  ////   closest (Closest)
  object Closest extends LibFcn {
    val name = prefix + "closest"
    val sig = Sig(List(
      "datum" -> P.Array(P.Wildcard("A")),
      "clusters" -> P.Array(P.WildRecord("C", ListMap("center" -> P.Array(P.Wildcard("B"))))),
      "metric" -> P.Fcn(List(P.Array(P.Wildcard("A")), P.Array(P.Wildcard("B"))), P.Double)
    ), P.Wildcard("C"))
    val doc =
      <doc>
        <desc>Find the cluster <tp>C</tp> whose <pf>center</pf> is closest to the <p>datum</p>, according to the <p>metric</p>.</desc>
        <param name="datum">Sample datum.</param>
        <param name="clusters">Set of clusters; the record type <tp>C</tp> may contain additional identifying information for post-processing.</param>
        <param name="metric">Function used to compare each <p>datum</p> with the <pf>center</pf> of the <p>clusters</p>.  (See, for example, <f>metric.euclidean</f>.)</param>
        <ret>Returns the closest cluster record.</ret>
        <error>Raises a "no clusters" error if <p>clusters</p> is empty.</error>
      </doc>
    def apply[A, B](datum: PFAArray[A], clusters: PFAArray[PFARecord], metric: (PFAArray[A], PFAArray[B]) => Double): PFARecord = {
      val vector = clusters.toVector
      val length = vector.size
      if (length == 0)
        throw new PFARuntimeException("no clusters")
      var bestRecord: PFARecord = null
      var bestDistance = 0.0
      var i = 0
      while (i < length) {
        val thisRecord = vector(i)
        val thisDistance = metric(datum, thisRecord.get("center").asInstanceOf[PFAArray[B]])
        if (bestRecord == null  ||  thisDistance < bestDistance) {
          bestRecord = thisRecord
          bestDistance = thisDistance
        }
        i += 1
      }
      bestRecord
    }
  }
  provide(Closest)

  ////   closestN (ClosestN)
  object ClosestN extends LibFcn {
    val name = prefix + "closestN"
    val sig = Sig(List(
      "datum" -> P.Array(P.Wildcard("A")),
      "clusters" -> P.Array(P.WildRecord("C", ListMap("center" -> P.Array(P.Wildcard("B"))))),
      "metric" -> P.Fcn(List(P.Array(P.Wildcard("A")), P.Array(P.Wildcard("B"))), P.Double),
      "n" -> P.Int
    ), P.Array(P.Wildcard("C")))
    val doc =
      <doc>
        <desc>Find the <p>n</p> clusters <tp>C</tp> whose <pf>centers</pf> are closest to the <p>datum</p>, according to the <p>metric</p>.</desc>
        <param name="datum">Sample datum.</param>
        <param name="clusters">Set of clusters; the record type <tp>C</tp> may contain additional identifying information for post-processing.</param>
        <param name="metric">Function used to compare each <p>datum</p> with the <pf>center</pf> of the <p>clusters</p>.  (See, for example, <f>metric.euclidean</f>.)</param>
        <param name="n">Number of clusters to search for.</param>
        <ret>An array of the closest cluster records in order from the closest to the farthest.  The length of the array is minimum of <p>n</p> and the length of <p>clusters</p>.</ret>
        <detail>Note that this method can be used to implement k-nearest neighbors.</detail>
      </doc>
    def apply[A, B](datum: PFAArray[A], clusters: PFAArray[PFARecord], metric: (PFAArray[A], PFAArray[B]) => Double, n: Int): PFAArray[PFARecord] = {
      val vector = clusters.toVector
      val distances = vector map {record => record -> metric(datum, record.get("center").asInstanceOf[PFAArray[B]])} toMap

      val indexes = argLowestN(vector, n, (cluster1: PFARecord, cluster2: PFARecord) => distances(cluster1) < distances(cluster2))

      PFAArray.fromVector(indexes map {i => vector(i)})
    }
  }
  provide(ClosestN)

  ////   randomSeeds (RandomSeeds)
  object RandomSeeds extends LibFcn {
    val name = prefix + "randomSeeds"
    val sig = Sig(List(
      "data" -> P.Array(P.Array(P.Wildcard("A"))),
      "k" -> P.Int,
      "newCluster" -> P.Fcn(List(P.Int, P.Array(P.Wildcard("A"))), P.WildRecord("C", ListMap("center" -> P.Array(P.Wildcard("B")))))
    ), P.Array(P.Wildcard("C")))
    val doc =
      <doc>
        <desc>Call <p>newCluster</p> to create <p>k</p> cluster records with random, unique cluster centers drawn from <p>data</p>.</desc>
        <param name="data">Sample data.</param>
        <param name="k">Number of times to call <p>newCluster</p>.</param>
        <param name="newCluster">Function that creates a cluster record, given an index (ranges from zero up to but not including <p>k</p>) and a random vector from <p>data</p>.</param>
        <ret>The cluster records created by <p>newCluster</p>.</ret>
        <error>Raises a "k must be greater than zero" error if <p>k</p> is less than or equal to zero.</error>
        <error>Raises a "not enough unique points" error if <p>data</p> has fewer than <p>k</p> unique elements.</error>
        <error>Raises a "dimensions of vectors do not match" error if the elements of <p>data</p> are not all the same size.</error>
      </doc>
    override def javaRef(fcnType: FcnType): JavaCode =
      JavaCode("(new " + this.getClass.getName + "Selector(randomGenerator()))")
    class Selector(randomGenerator: Random) {
      def apply[X](data: PFAArray[PFAArray[X]], k: Int, newCluster: (Int, PFAArray[X]) => PFARecord): PFAArray[PFARecord] = {
        if (k <= 0)
          throw new PFARuntimeException("k must be greater than zero")

        val uniques = data.toVector.distinct
        if (uniques.size < k)
          throw new PFARuntimeException("not enough unique points")

        val sizes = uniques.map(_.toVector.size).distinct
        if (sizes.size != 1)
          throw new PFARuntimeException("dimensions of vectors do not match")

        val selected = randomGenerator.shuffle(uniques).take(k)

        PFAArray.fromVector(
          for ((vec, i) <- selected.zipWithIndex) yield
            newCluster(i, vec))
      }
    }
  }
  provide(RandomSeeds)

  ////   kmeansIteration (KMeansIteration)
  object KMeansIteration extends LibFcn {
    val name = prefix + "kmeansIteration"
    val sig = Sig(List(
      "data" -> P.Array(P.Array(P.Wildcard("A"))),
      "clusters" -> P.Array(P.WildRecord("C", ListMap("center" -> P.Array(P.Wildcard("B"))))),
      "metric" -> P.Fcn(List(P.Array(P.Wildcard("A")), P.Array(P.Wildcard("B"))), P.Double),
      "update" -> P.Fcn(List(P.Array(P.Array(P.Wildcard("A"))), P.Wildcard("C")), P.Wildcard("C"))
    ), P.Array(P.Wildcard("C")))
    val doc =
      <doc>
        <desc>Update a cluster set by applying one iteration of k-means (Lloyd's algorithm).</desc>
        <param name="data">Sample data.</param>
        <param name="clusters">Set of clusters; the record type <tp>C</tp> may contain additional identifying information for post-processing.</param>
        <param name="metric">Function used to compare each <p>datum</p> with the <pf>center</pf> of the <p>clusters</p>.  (See, for example, <f>metric.euclidean</f>.)</param>
        <param name="update">Function of matched data and old cluster records that yields new cluster records.  (See, for example, <f>model.cluster.updateMean</f> with <p>weight</p> = 0.)</param>
        <detail>The <p>update</p> function is only called if the number of matched data points is greater than zero.</detail>
        <ret>Returns a new cluster set with each of the <tp>centers</tp> located at the average of all points that match the corresponding cluster in the old cluster set.</ret>
        <error>Raises a "no data" error if <p>data</p> is empty.</error>
        <error>Raises a "no clusters" error if <p>clusters</p> is empty.</error>
      </doc>
    def apply[A, B](data: PFAArray[PFAArray[A]], clusters: PFAArray[PFARecord], metric: (PFAArray[A], PFAArray[B]) => Double, update: (PFAArray[PFAArray[A]], PFARecord) => PFARecord): PFAArray[PFARecord] = {
      if (data.toVector.isEmpty)
        throw new PFARuntimeException("no data")

      val theClusters = clusters.toVector
      val centers = theClusters map {_.get("center").asInstanceOf[PFAArray[B]]}

      val length = theClusters.size
      if (length == 0)
        throw new PFARuntimeException("no clusters")

      val matched = Array.fill(length)(List[PFAArray[A]]())

      for (datum <- data.toVector) {
        var besti = 0
        var bestCenter: PFAArray[B] = null
        var bestDistance = 0.0
        var i = 0
        while (i < length) {
          val thisCenter = centers(i)
          val thisDistance = metric(datum, thisCenter)
          if (bestCenter == null  ||  thisDistance < bestDistance) {
            besti = i
            bestCenter = thisCenter
            bestDistance = thisDistance
          }
          i += 1
        }
        matched(besti) = datum :: matched(besti)
      }

      PFAArray.fromVector(
        for ((matchedData, i) <- matched.toVector.zipWithIndex) yield
          if (matchedData.isEmpty)
            theClusters(i)
          else
            update(PFAArray.fromVector(matchedData.reverse.toVector), theClusters(i)))
    }
  }
  provide(KMeansIteration)

  ////   updateMean (UpdateMean)
  object UpdateMean extends LibFcn {
    val name = prefix + "updateMean"
    val sig = Sig(List("data" -> P.Array(P.Array(P.Double)), "cluster" -> P.WildRecord("C", ListMap("center" -> P.Array(P.Double))), "weight" -> P.Double), P.Wildcard("C"))
    val doc =
      <doc>
        <desc>Update a cluster record by computing the mean of the <p>data</p> vectors and <p>weight</p> times the old <p>cluster</p> center.</desc>
        <detail>If <p>weight</p> is zero, the new center is equal to the mean of <p>data</p>, ignoring the old <p>center</p>.</detail>
        <error>Raises a "no data" error if <p>data</p> is empty.</error>
        <error>Raises a "dimensions of vectors do not match" error if all elements of <p>data</p> and the <p>cluster</p> center do not match.</error>
      </doc>
    def apply(data: PFAArray[PFAArray[Double]], cluster: PFARecord, weight: Double): PFARecord = {
      if (data.toVector.isEmpty)
        throw new PFARuntimeException("no data")

      val dimension = data.toVector.head.size
      val summ = Array.fill(dimension)(0.0)

      for (datum <- data.toVector) {
        val vec = datum.toVector
        if (vec.size != dimension)
          throw new PFARuntimeException("dimensions of vectors do not match")
        for (i <- 0 until dimension)
          summ(i) += vec(i)
      }

      val vec = cluster.get("center").asInstanceOf[PFAArray[Double]].toVector
      if (vec.size != dimension)
        throw new PFARuntimeException("dimensions of vectors do not match")
      for (i <- 0 until dimension)
        summ(i) += weight * vec(i)

      val denom = data.toVector.size + weight
      for (i <- 0 until dimension)
        summ(i) = summ(i) / denom

      cluster.multiUpdate(Array("center"), Array(PFAArray.fromVector(summ.toVector)))
    }
  }
  provide(UpdateMean)

}
