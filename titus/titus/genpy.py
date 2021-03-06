#!/usr/bin/env python

# Copyright (C) 2014  Open Data ("Open Data" refers to
# one or more of the following companies: Open Data Partners LLC,
# Open Data Research LLC, or Open Data Capital LLC.)
# 
# This file is part of Hadrian.
# 
# Licensed under the Hadrian Personal Use and Evaluation License (PUEL);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://raw.githubusercontent.com/opendatagroup/hadrian/master/LICENSE
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import math
import threading
import time
import random

from avro.datafile import DataFileReader, DataFileWriter
from avro.io import DatumReader, DatumWriter

from titus.errors import *
import titus.pfaast
import titus.datatype
import titus.fcn
import titus.options
import titus.P as P
import titus.reader
import titus.signature
import titus.util
from titus.util import DynamicScope

from titus.pfaast import EngineConfig
from titus.pfaast import Cell as AstCell
from titus.pfaast import Pool as AstPool
from titus.pfaast import FcnDef
from titus.pfaast import FcnRef
from titus.pfaast import FcnRefFill
from titus.pfaast import CallUserFcn
from titus.pfaast import Call
from titus.pfaast import Ref
from titus.pfaast import LiteralNull
from titus.pfaast import LiteralBoolean
from titus.pfaast import LiteralInt
from titus.pfaast import LiteralLong
from titus.pfaast import LiteralFloat
from titus.pfaast import LiteralDouble
from titus.pfaast import LiteralString
from titus.pfaast import LiteralBase64
from titus.pfaast import Literal
from titus.pfaast import NewObject
from titus.pfaast import NewArray
from titus.pfaast import Do
from titus.pfaast import Let
from titus.pfaast import SetVar
from titus.pfaast import AttrGet
from titus.pfaast import AttrTo
from titus.pfaast import CellGet
from titus.pfaast import CellTo
from titus.pfaast import PoolGet
from titus.pfaast import PoolTo
from titus.pfaast import If
from titus.pfaast import Cond
from titus.pfaast import While
from titus.pfaast import DoUntil
from titus.pfaast import For
from titus.pfaast import Foreach
from titus.pfaast import Forkeyval
from titus.pfaast import CastCase
from titus.pfaast import CastBlock
from titus.pfaast import Upcast
from titus.pfaast import IfNotNull
from titus.pfaast import Doc
from titus.pfaast import Error
from titus.pfaast import Try
from titus.pfaast import Log

from titus.pfaast import Method
from titus.pfaast import ArrayIndex
from titus.pfaast import MapIndex
from titus.pfaast import RecordIndex

class GeneratePython(titus.pfaast.Task):
    @staticmethod
    def makeTask(style):
        if style == "pure":
            return GeneratePythonPure()
        else:
            raise NotImplementedError("unrecognized style " + style)

    def commandsMap(self, codes, indent):
        suffix = indent + "self.actionsFinished += 1\n" + \
                 indent + "return last\n"
        return "".join(indent + x + "\n" for x in codes[:-1]) + indent + "last = " + codes[-1] + "\n" + suffix

    def commandsEmit(self, codes, indent):
        suffix = indent + "self.actionsFinished += 1\n"
        return "".join(indent + x + "\n" for x in codes) + suffix

    def commandsFold(self, codes, indent):
        prefix = indent + "scope.let({'tally': self.tally})\n"
        suffix = indent + "self.tally = last\n" + \
                 indent + "self.actionsFinished += 1\n" + \
                 indent + "return self.tally\n"
        return prefix + "".join(indent + x + "\n" for x in codes[:-1]) + indent + "last = " + codes[-1] + "\n" + suffix

    def commandsBeginEnd(self, codes, indent):
        return "".join(indent + x + "\n" for x in codes)

    def reprPath(self, path):
        out = []
        for p in path:
            if isinstance(p, ArrayIndex):
                out.append(p.i)
            elif isinstance(p, MapIndex):
                out.append(p.k)
            elif isinstance(p, RecordIndex):
                out.append(repr(p.f))
            else:
                raise Exception
        return ", ".join(out)

    def __call__(self, context, engineOptions):
        if isinstance(context, EngineConfig.Context):
            if context.name is None:
                name = titus.util.uniqueEngineName()
            else:
                name = context.name

            begin, beginSymbols, beginCalls = context.begin
            action, actionSymbols, actionCalls = context.action
            end, endSymbols, endCalls = context.end

            callGraph = {"(begin)": beginCalls, "(action)": actionCalls, "(end)": endCalls}
            for fname, fctx in context.fcns:
                callGraph[fname] = fctx.calls

            out = ["class PFA_" + name + """(PFAEngine):
    def __init__(self, cells, pools, config, options, log, emit, zero, rand):
        self.actionsStarted = 0
        self.actionsFinished = 0
        self.cells = cells
        self.pools = pools
        self.config = config
        self.options = options
        self.log = log
        self.emit = emit
        self.rand = rand
        self.callGraph = """ + repr(callGraph) + "\n"]

            if context.method == Method.FOLD:
                out.append("        self.tally = zero\n")

            out.append("""    def initialize(self):
        self
""")

            for ufname, fcnContext in context.fcns:
                out.append("        self.f[" + repr(ufname) + "] = " + self(fcnContext, engineOptions) + "\n")

            if len(begin) > 0:
                out.append("""
    def begin(self):
        state = ExecutionState(self.options, self.rand, 'action', self.parser)
        scope = DynamicScope(None)
        scope.let({'name': self.config.name, 'metadata': self.config.metadata})
        if self.config.version is not None:
            scope.let({'version': self.config.version})
""" + self.commandsBeginEnd(begin, "        "))

            if context.method == Method.MAP:
                commands = self.commandsMap(action, "            ")
            elif context.method == Method.EMIT:
                commands = self.commandsEmit(action, "            ")
            elif context.method == Method.FOLD:
                commands = self.commandsFold(action, "            ")

            out.append("""
    def action(self, input):
        state = ExecutionState(self.options, self.rand, 'action', self.parser)
        scope = DynamicScope(None)
        for cell in self.cells.values():
            cell.maybeSaveBackup()
        for pool in self.pools.values():
            pool.maybeSaveBackup()
        self.actionsStarted += 1
        try:
            scope.let({'input': input, 'name': self.config.name, 'metadata': self.config.metadata, 'actionsStarted': self.actionsStarted, 'actionsFinished': self.actionsFinished})
            if self.config.version is not None:
                scope.let({'version': self.config.version})
""" + commands)

            out.append("""        except Exception:
            for cell in self.cells.values():
                cell.maybeRestoreBackup()
            for pool in self.pools.values():
                pool.maybeRestoreBackup()
            raise
""")

            if len(end) > 0:
                tallyLine = ""
                if context.method == Method.FOLD:
                    tallyLine = """        scope.let({'tally': self.tally})\n"""
                
                out.append("""
    def end(self):
        state = ExecutionState(self.options, self.rand, 'action', self.parser)
        scope = DynamicScope(None)
        scope.let({'name': self.config.name, 'metadata': self.config.metadata, 'actionsStarted': self.actionsStarted, 'actionsFinished': self.actionsFinished})
        if self.config.version is not None:
            scope.let({'version': self.config.version})
""" + tallyLine + self.commandsBeginEnd(end, "        "))

            return "".join(out)

        elif isinstance(context, FcnDef.Context):
            return "labeledFcn(lambda state, scope: do(" + ", ".join(context.exprs) + "), [" + ", ".join(map(repr, context.paramNames)) + "])"

        elif isinstance(context, FcnRef.Context):
            return "self.f[" + repr(context.fcn.name) + "]"

        elif isinstance(context, FcnRefFill.Context):
            reducedArgs = ["\"$" + str(x) + "\"" for x in xrange(len(context.fcnType.params))]
            j = 0
            args = []
            for name in context.originalParamNames:
                if name in context.argTypeResult:
                    args.append(context.argTypeResult[name][1])
                else:
                    args.append("scope.get(\"$" + str(j) + "\")")
                    j += 1

            return "labeledFcn(lambda state, scope: call(state, DynamicScope(scope), self.f[" + repr(context.fcn.name) + "], [" + ", ".join(args) + "]), [" + ", ".join(reducedArgs) + "])"

        elif isinstance(context, CallUserFcn.Context):
            return "call(state, DynamicScope(None), self.f['u.' + " + context.name + "], [" + ", ".join(context.args) + "])"

        elif isinstance(context, Call.Context):
            return context.fcn.genpy(context.paramTypes, context.args)

        elif isinstance(context, Ref.Context):
            return "scope.get({})".format(repr(context.name))

        elif isinstance(context, LiteralNull.Context):
            return "None"

        elif isinstance(context, LiteralBoolean.Context):
            return str(context.value)

        elif isinstance(context, LiteralInt.Context):
            return str(context.value)

        elif isinstance(context, LiteralLong.Context):
            return str(context.value)

        elif isinstance(context, LiteralFloat.Context):
            return str(float(context.value))

        elif isinstance(context, LiteralDouble.Context):
            return str(float(context.value))

        elif isinstance(context, LiteralString.Context):
            return repr(context.value)

        elif isinstance(context, LiteralBase64.Context):
            return repr(context.value)

        elif isinstance(context, Literal.Context):
            return repr(titus.datatype.jsonDecoder(context.retType, json.loads(context.value)))

        elif isinstance(context, NewObject.Context):
            return "{" + ", ".join(repr(k) + ": " + v for k, v in context.fields.items()) + "}"

        elif isinstance(context, NewArray.Context):
            return "[" + ", ".join(context.items) + "]"

        elif isinstance(context, Do.Context):
            return "do(" + ", ".join(context.exprs) + ")"

        elif isinstance(context, Let.Context):
            return "scope.let({" + ", ".join(repr(n) + ": " + e for n, t, e in context.nameTypeExpr) + "})"

        elif isinstance(context, SetVar.Context):
            return "scope.set({" + ", ".join(repr(n) + ": " + e for n, t, e in context.nameTypeExpr) + "})"

        elif isinstance(context, AttrGet.Context):
            return "get(" + context.expr + ", [" + self.reprPath(context.path) + "])"

        elif isinstance(context, AttrTo.Context):
            return "update(state, scope, {}, [{}], {})".format(context.expr, self.reprPath(context.path), context.to)

        elif isinstance(context, CellGet.Context):
            return "get(self.cells[{}].value, [{}])".format(repr(context.cell), self.reprPath(context.path))

        elif isinstance(context, CellTo.Context):
            return "self.cells[{}].update(state, scope, [{}], {})".format(repr(context.cell), self.reprPath(context.path), context.to)

        elif isinstance(context, PoolGet.Context):
            return "get(self.pools[{}].value, [{}])".format(repr(context.pool), self.reprPath(context.path))

        elif isinstance(context, PoolTo.Context):
            return "self.pools[{}].update(state, scope, [{}], {}, {})".format(repr(context.pool), self.reprPath(context.path), context.to, context.init)

        elif isinstance(context, If.Context):
            if context.elseClause is None:
                return "ifThen(state, scope, lambda state, scope: {}, lambda state, scope: do({}))".format(context.predicate, ", ".join(context.thenClause))
            else:
                return "ifThenElse(state, scope, lambda state, scope: {}, lambda state, scope: do({}), lambda state, scope: do({}))".format(context.predicate, ", ".join(context.thenClause), ", ".join(context.elseClause))

        elif isinstance(context, Cond.Context):
            if not context.complete:
                return "cond(state, scope, [{}])".format(", ".join("(lambda state, scope: {}, lambda state, scope: do({}))".format(walkBlock.pred, ", ".join(walkBlock.exprs)) for walkBlock in context.walkBlocks))
            else:
                return "condElse(state, scope, [{}], lambda state, scope: do({}))".format(", ".join("(lambda state, scope: {}, lambda state, scope: do({}))".format(walkBlock.pred, ", ".join(walkBlock.exprs)) for walkBlock in context.walkBlocks[:-1]), ", ".join(context.walkBlocks[-1].exprs))

        elif isinstance(context, While.Context):
            return "doWhile(state, scope, lambda state, scope: {}, lambda state, scope: do({}))".format(context.predicate, ", ".join(context.loopBody))

        elif isinstance(context, DoUntil.Context):
            return "doUntil(state, scope, lambda state, scope: {}, lambda state, scope: do({}))".format(context.predicate, ", ".join(context.loopBody))

        elif isinstance(context, For.Context):
            return "doFor(state, scope, lambda state, scope: scope.let({" + ", ".join(repr(n) + ": " + e for n, t, e in context.initNameTypeExpr) + "}), lambda state, scope: " + context.predicate + ", lambda state, scope: scope.set({" + ", ".join(repr(n) + ": " + e for n, t, e in context.stepNameTypeExpr) + "}), lambda state, scope: do(" + ", ".join(context.loopBody) + "))"

        elif isinstance(context, Foreach.Context):
            return "doForeach(state, scope, {}, {}, lambda state, scope: do({}))".format(repr(context.name), context.objExpr, ", ".join(context.loopBody))

        elif isinstance(context, Forkeyval.Context):
            return "doForkeyval(state, scope, {}, {}, {}, lambda state, scope: do({}))".format(repr(context.forkey), repr(context.forval), context.objExpr, ", ".join(context.loopBody))

        elif isinstance(context, CastCase.Context):
            return "(" + repr(context.name) + ", " + repr(context.toType) + ", lambda state, scope: do(" + ", ".join(context.clause) + "))"

        elif isinstance(context, CastBlock.Context):
            return "cast(state, scope, " + context.expr + ", " + repr(context.exprType) + ", [" + ", ".join(caseRes for castCtx, caseRes in context.cases) + "], " + repr(context.partial) + ", self.parser)"

        elif isinstance(context, Upcast.Context):
            return context.expr

        elif isinstance(context, IfNotNull.Context):
            if context.elseClause is None:
                return "ifNotNull(state, scope, {" + ", ".join(repr(n) + ": " + e for n, t, e in context.symbolTypeResult) + "}, {" + ", ".join(repr(n) + ": '" + repr(t) + "'" for n, t, e in context.symbolTypeResult) + "}, lambda state, scope: do(" + ", ".join(context.thenClause) + "))"
            else:
                return "ifNotNullElse(state, scope, {" + ", ".join(repr(n) + ": " + e for n, t, e in context.symbolTypeResult) + "}, {" + ", ".join(repr(n) + ": '" + repr(t) + "'" for n, t, e in context.symbolTypeResult) + "}, lambda state, scope: do(" + ", ".join(context.thenClause) + "), lambda state, scope: do(" + ", ".join(context.elseClause) + "))"

        elif isinstance(context, Doc.Context):
            return "None"

        elif isinstance(context, Error.Context):
            return "error(" + repr(context.message) + ", " + repr(context.code) + ")"

        elif isinstance(context, Try.Context):
            return "tryCatch(state, scope, lambda state, scope: do(" + ", ".join(context.exprs) + "), " + repr(context.filter) + ")"

        elif isinstance(context, Log.Context):
            return "self.log([{}], {})".format(", ".join(x[1] for x in context.exprTypes), repr(context.namespace))

        else:
            raise PFASemanticException("unrecognized context class: " + str(type(context)), "")

class GeneratePythonPure(GeneratePython):
    pass

###########################################################################

class ExecutionState(object):
    def __init__(self, options, rand, routine, parser):
        self.rand = rand
        self.parser = parser

        if routine == "begin":
            self.timeout = options.timeout_begin
        elif routine == "action":
            self.timeout = options.timeout_action
        elif routine == "end":
            self.timeout = options.timeout_end

        self.startTime = time.time()

    def checkTime(self):
        if self.timeout > 0 and (time.time() - self.startTime) * 1000 > self.timeout:
            raise PFATimeoutException("exceeded timeout of {} milliseconds".format(self.timeout))

class SharedState(object):
    def __init__(self):
        self.cells = {}
        self.pools = {}

    def __repr__(self):
        return "SharedState({} cells, {} pools)".format(len(self.cells), len(self.pools))

class PersistentStorageItem(object):
    def __init__(self, value, shared, rollback):
        self.value = value
        self.shared = shared
        self.rollback = rollback

class Cell(PersistentStorageItem):
    def __init__(self, value, shared, rollback):
        if shared:
            self.lock = threading.Lock()
        super(Cell, self).__init__(value, shared, rollback)

    def __repr__(self):
        contents = repr(self.value)
        if len(contents) > 30:
            contents = contents[:27] + "..."
        return "Cell(" + ("shared, " if self.shared else "") + ("rollback, " if self.rollback else "") + contents + ")"
            
    def update(self, state, scope, path, to):
        result = None
        if self.shared:
            self.lock.acquire()
            self.value = update(state, scope, self.value, path, to)
            result = self.value
            self.lock.release()
        else:
            self.value = update(state, scope, self.value, path, to)
            result = self.value
        return result

    def maybeSaveBackup(self):
        if self.rollback:
            self.oldvalue = self.value

    def maybeRestoreBackup(self):
        if self.rollback:
            self.value = self.oldvalue

class Pool(PersistentStorageItem):
    def __init__(self, value, shared, rollback):
        if shared:
            self.locklock = threading.Lock()
            self.locks = {}
        super(Pool, self).__init__(value, shared, rollback)

    def __repr__(self):
        contents = repr(self.value)
        if len(contents) > 30:
            contents = contents[:27] + "..."
        return "Pool(" + ("shared, " if self.shared else "") + ("rollback, " if self.rollback else "") + contents + ")"

    def update(self, state, scope, path, to, init):
        result = None

        head, tail = path[0], path[1:]

        if self.shared:
            self.locklock.acquire()
            if head in self.locks:
                self.locks[head].acquire()
            else:
                self.locks[head] = threading.Lock()
                self.locks[head].acquire()
            self.locklock.release()

            if head not in self.value:
                self.value[head] = init
            self.value[head] = update(state, scope, self.value[head], tail, to)

            result = self.value[head]
            self.locks[head].release()

        else:
            if head not in self.value:
                self.value[head] = init
            self.value[head] = update(state, scope, self.value[head], tail, to)
            result = self.value[head]

        return result

    def maybeSaveBackup(self):
        if self.rollback:
            self.oldvalue = dict(self.value)

    def maybeRestoreBackup(self):
        if self.rollback:
            self.value = self.oldvalue

def labeledFcn(fcn, paramNames):
    fcn.paramNames = paramNames
    return fcn

def get(obj, path):
    while len(path) > 0:
        head, tail = path[0], path[1:]
        try:
            obj = obj[head]
        except (KeyError, IndexError):
            if isinstance(obj, (list, tuple)):
                raise PFARuntimeException("index {} out of bounds for array of size {}".format(head, len(obj)))
            else:
                raise PFARuntimeException("key \"{}\" not found in map with size {}".format(head, len(obj)))
        path = tail

    return obj

def update(state, scope, obj, path, to):
    if len(path) > 0:
        head, tail = path[0], path[1:]

        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == head:
                    out[k] = update(state, scope, v, tail, to)
                else:
                    out[k] = v
            return out

        elif isinstance(obj, (list, tuple)):
            out = []
            for i, x in enumerate(obj):
                if i == head:
                    out.append(update(state, scope, x, tail, to))
                else:
                    out.append(x)
            return out

        else:
            raise Exception

    elif callable(to):
        callScope = DynamicScope(scope)
        callScope.let({to.paramNames[0]: obj})
        return to(state, callScope)

    else:
        return to
        
def do(*exprs):
    # You've already done them; just return the right value.
    if len(exprs) > 0:
        return exprs[-1]
    else:
        return None

def ifThen(state, scope, predicate, thenClause):
    if predicate(state, DynamicScope(scope)):
        thenClause(state, DynamicScope(scope))
    return None

def ifThenElse(state, scope, predicate, thenClause, elseClause):
    if predicate(state, DynamicScope(scope)):
        return thenClause(state, DynamicScope(scope))
    else:
        return elseClause(state, DynamicScope(scope))

def cond(state, scope, ifThens):
    for predicate, thenClause in ifThens:
        if predicate(state, DynamicScope(scope)):
            thenClause(state, DynamicScope(scope))
            break
    return None

def condElse(state, scope, ifThens, elseClause):
    for predicate, thenClause in ifThens:
        if predicate(state, DynamicScope(scope)):
            return thenClause(state, DynamicScope(scope))
    return elseClause(state, DynamicScope(scope))
    
def doWhile(state, scope, predicate, loopBody):
    bodyScope = DynamicScope(scope)
    predScope = DynamicScope(bodyScope)
    while predicate(state, predScope):
        state.checkTime()
        loopBody(state, bodyScope)
    return None
    
def doUntil(state, scope, predicate, loopBody):
    bodyScope = DynamicScope(scope)
    predScope = DynamicScope(bodyScope)
    while True:
        state.checkTime()
        loopBody(state, bodyScope)
        if predicate(state, predScope):
            break
    return None

def doFor(state, scope, initLet, predicate, stepSet, loopBody):
    loopScope = DynamicScope(scope)
    predScope = DynamicScope(loopScope)
    bodyScope = DynamicScope(loopScope)
    initLet(state, loopScope)
    while predicate(state, predScope):
        state.checkTime()
        loopBody(state, bodyScope)
        stepSet(state, loopScope)
    return None

def doForeach(state, scope, name, array, loopBody):
    loopScope = DynamicScope(scope)
    bodyScope = DynamicScope(loopScope)
    for item in array:
        state.checkTime()
        loopScope.let({name: item})
        loopBody(state, bodyScope)
    return None

def doForkeyval(state, scope, forkey, forval, mapping, loopBody):
    loopScope = DynamicScope(scope)
    bodyScope = DynamicScope(loopScope)
    for key, val in mapping.items():
        state.checkTime()
        loopScope.let({forkey: key, forval: val})
        loopBody(state, bodyScope)
    return None
        
def cast(state, scope, expr, fromType, cases, partial, parser):
    fromType = parser.getAvroType(fromType)

    for name, toType, clause in cases:
        toType = parser.getAvroType(toType)

        if isinstance(fromType, titus.datatype.AvroUnion) and isinstance(expr, dict) and len(expr) == 1:
            tag, = expr.keys()
            value, = expr.values()

            if not ((tag == toType.name) or \
                    (tag == "int" and toType.name in ("long", "float", "double")) or \
                    (tag == "long" and toType.name in ("float", "double")) or \
                    (tag == "float" and toType.name == "double")):
                continue

        else:
            value = expr

        try:
            castValue = titus.datatype.jsonDecoder(toType, value)
        except (AvroException, TypeError):
            pass
        else:
            clauseScope = DynamicScope(scope)
            clauseScope.let({name: castValue})
            out = clause(state, clauseScope)

            if partial:
                return None
            else:
                return out
    return None

def untagUnions(nameExpr, nameType):
    out = {}
    for name, expr in nameExpr.items():
        if isinstance(expr, dict) and len(expr) == 1:
            tag, = expr.keys()
            value, = expr.values()

            expectedTag = json.loads(nameType[name])
            if isinstance(expectedTag, dict):
                if expectedTag["type"] in ("record", "enum", "fixed"):
                    if "namespace" in expectedTag and expectedTag["namespace"].strip() != "":
                        expectedTag = expectedTag["namespace"] + "." + expectedTag["name"]
                    else:
                        expectedTag = expectedTag["name"]
                else:
                    expectedTag = expectedTag["type"]

            if tag == expectedTag:
                out[name] = value
            else:
                out[name] = expr
        else:
            out[name] = expr

    return out

def ifNotNull(state, scope, nameExpr, nameType, thenClause):
    if all(x is not None for x in nameExpr.values()):
        thenScope = DynamicScope(scope)
        thenScope.let(untagUnions(nameExpr, nameType))
        thenClause(state, thenScope)

def ifNotNullElse(state, scope, nameExpr, nameType, thenClause, elseClause):
    if all(x is not None for x in nameExpr.values()):
        thenScope = DynamicScope(scope)
        thenScope.let(untagUnions(nameExpr, nameType))
        return thenClause(state, thenScope)
    else:
        return elseClause(state, scope)

def error(message, code):
    raise PFAUserException(message, code)

def tryCatch(state, scope, exprs, filter):
    try:
        return exprs(state, scope)
    except Exception as err:
        if filter is None or err.message in filter:
            return None
        else:
            raise err

def genericLog(message, namespace):
    if namespace is None:
        print " ".join(map(json.dumps, message))
    else:
        print namespace + ": " + " ".join(map(json.dumps, message))
    
class FakeEmitForExecution(titus.fcn.Fcn):
    def __init__(self, engine):
        self.engine = engine

def genericEmit(x):
    pass

def checkForDeadlock(engineConfig, engine):
    class WithFcnRef(object):
        def isDefinedAt(self, ast):
            return isinstance(ast, (CellTo, PoolTo)) and isinstance(ast.to, (FcnRef, FcnRefFill))
        def __call__(self, slotTo):
            if engine.hasSideEffects(slotTo.to.name):
                raise PFAInitializationException("{} references function \"{}\", which has side-effects".format(slotTo.desc, slotTo.to.name))
    engineConfig.collect(WithFcnRef())

    class CellToOrPoolTo(object):
        def isDefinedAt(self, ast):
            return isinstance(ast, (CellTo, PoolTo))
        def __call__(self, slotTo):
            raise PFAInitializationException("inline function in cell-to or pool-to invokes a " + slotTo.desc)

    class SideEffectFunction(object):
        def isDefinedAt(self, ast):
            return isinstance(ast, Call) and engine.hasSideEffects(ast.name)
        def __call__(self, call):
            raise PFAInitializationException("inline function in cell-to or pool-to invokes function \"{}\", which has side-effects".format(call.name))

    class WithFcnDef(object):
        def isDefinedAt(self, ast):
            return isinstance(ast, (CellTo, PoolTo)) and isinstance(ast.to, FcnDef)
        def __call__(self, slotTo):
            for x in slotTo.to.body:
                x.collect(CellToOrPoolTo())
                x.collect(SideEffectFunction())
    engineConfig.collect(WithFcnDef())

class PFAEngine(object):
    @staticmethod
    def fromAst(engineConfig, options=None, sharedState=None, multiplicity=1, style="pure", debug=False):
        functionTable = titus.pfaast.FunctionTable.blank()

        engineOptions = titus.options.EngineOptions(engineConfig.options, options)

        context, code = engineConfig.walk(GeneratePython.makeTask(style), titus.pfaast.SymbolTable.blank(), functionTable, engineOptions)
        if debug:
            print code

        sandbox = {# Scoring engine architecture
                   "PFAEngine": PFAEngine,
                   "ExecutionState": ExecutionState,
                   "DynamicScope": DynamicScope,
                   # Python statement --> expression wrappers
                   "labeledFcn": labeledFcn,
                   "call": titus.util.callfcn,
                   "get": get,
                   "update": update,
                   "do": do,
                   "ifThen": ifThen,
                   "ifThenElse": ifThenElse,
                   "cond": cond,
                   "condElse": condElse,
                   "doWhile": doWhile,
                   "doUntil": doUntil,
                   "doFor": doFor,
                   "doForeach": doForeach,
                   "doForkeyval": doForkeyval,
                   "cast": cast,
                   "ifNotNull": ifNotNull,
                   "ifNotNullElse": ifNotNullElse,
                   "error": error,
                   "tryCatch": tryCatch,
                   # Python libraries
                   "math": math,
                   }

        exec(code, sandbox)
        cls = [x for x in sandbox.values() if getattr(x, "__bases__", None) == (PFAEngine,)][0]
        cls.parser = context.parser

        if sharedState is None:
            sharedState = SharedState()

        for cellName, cellConfig in engineConfig.cells.items():
            if cellConfig.shared and cellName not in sharedState.cells:
                value = titus.datatype.jsonDecoder(cellConfig.avroType, json.loads(cellConfig.init))
                sharedState.cells[cellName] = Cell(value, cellConfig.shared, cellConfig.rollback)

        for poolName, poolConfig in engineConfig.pools.items():
            if poolConfig.shared and poolName not in sharedState.pools:
                init = {}
                for k, v in poolConfig.init.items():
                    init[k] = json.loads(v)
                value = titus.datatype.jsonDecoder(titus.datatype.AvroMap(poolConfig.avroType), init)
                sharedState.pools[poolName] = Pool(value, poolConfig.shared, poolConfig.rollback)

        out = []
        for index in xrange(multiplicity):
            cells = dict(sharedState.cells)
            pools = dict(sharedState.pools)

            for cellName, cellConfig in engineConfig.cells.items():
                if not cellConfig.shared:
                    value = titus.datatype.jsonDecoder(cellConfig.avroType, json.loads(cellConfig.init))
                    cells[cellName] = Cell(value, cellConfig.shared, cellConfig.rollback)

            for poolName, poolConfig in engineConfig.pools.items():
                if not poolConfig.shared:
                    init = {}
                    for k, v in poolConfig.init.items():
                        init[k] = json.loads(v)
                    value = titus.datatype.jsonDecoder(titus.datatype.AvroMap(poolConfig.avroType), init)
                    pools[poolName] = Pool(value, poolConfig.shared, poolConfig.rollback)

            if engineConfig.method == Method.FOLD:
                zero = titus.datatype.jsonDecoder(engineConfig.output, json.loads(engineConfig.zero))
            else:
                zero = None

            if engineConfig.randseed is None:
                rand = random.Random()
            else:
                rand = random.Random(engineConfig.randseed)
            for skip in xrange(index):
                rand.random(skip)

            engine = cls(cells, pools, engineConfig, engineOptions, genericLog, genericEmit, zero, rand)

            f = dict(functionTable.functions)
            if engineConfig.method == Method.EMIT:
                f["emit"] = FakeEmitForExecution(engine)
            engine.f = f
            engine.config = engineConfig

            checkForDeadlock(engineConfig, engine)
            engine.initialize()

            out.append(engine)

        return out

    @staticmethod
    def fromJson(src, options=None, sharedState=None, multiplicity=1, style="pure", debug=False):
        return PFAEngine.fromAst(titus.reader.jsonToAst(src), options, sharedState, multiplicity, style, debug)

    @staticmethod
    def fromYaml(src, options=None, sharedState=None, multiplicity=1, style="pure", debug=False):
        return PFAEngine.fromAst(titus.reader.yamlToAst(src), options, sharedState, multiplicity, style, debug)

    def snapshot(self):
        newCells = dict((k, AstCell(self.config.cells[k].avroPlaceholder, json.dumps(v.value), v.shared, v.rollback)) for k, v in self.cells.items())
        newPools = dict((k, AstPool(self.config.pools[k].avroPlaceholder, dict((kk, json.dumps(vv)) for kk, vv in v.value.items()), v.shared, v.rollback)) for k, v in self.pools.items())

        return EngineConfig(
            self.config.name,
            self.config.method,
            self.config.inputPlaceholder,
            self.config.outputPlaceholder,
            self.config.begin,
            self.config.action,
            self.config.end,
            self.config.fcns,
            self.config.zero,
            newCells,
            newPools,
            self.config.randseed,
            self.config.doc,
            self.config.version,
            self.config.metadata,
            self.config.options)

    def calledBy(self, fcnName, exclude=None):
        if exclude is None:
            exclude = set()
        if fcnName in exclude:
            return set()
        else:
            if fcnName in self.callGraph:
                newExclude = exclude.union(set([fcnName]))
                nextLevel = set([])
                for f in self.callGraph[fcnName]:
                    nextLevel = nextLevel.union(self.calledBy(f, newExclude))
                return self.callGraph[fcnName].union(nextLevel)
            else:
                return set()

    def callDepth(self, fcnName, exclude=None, startingDepth=0):
        if exclude is None:
            exclude = set()
        if fcnName in exclude:
            return float("inf")
        else:
            if fcnName in self.callGraph:
                newExclude = exclude.union(set([fcnName]))
                deepest = startingDepth
                for f in self.callGraph[fcnName]:
                    fdepth = self.callDepth(f, newExclude, startingDepth + 1)
                    if fdepth > deepest:
                        deepest = fdepth
                return deepest
            else:
                return startingDepth

    def isRecursive(self, fcnName):
        return fcnName in self.calledBy(fcnName)

    def hasRecursive(self, fcnName):
        return self.callDepth(fcnName) == float("inf")

    def hasSideEffects(self, fcnName):
        reach = self.calledBy(fcnName)
        return CellTo.desc in reach or PoolTo.desc in reach

    def avroInputIterator(self, inputStream):
        try:
            import fastavro
            return fastavro.reader(inputStream)
        except ImportError:
            return DataFileReader(inputStream, DatumReader())

    def avroOutputDataFileWriter(self, fileName):
        return DataFileWriter(open(fileName, "w"), DatumWriter(), self.config.output.schema)
