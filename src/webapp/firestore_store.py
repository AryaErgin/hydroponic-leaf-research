"""Bounded transaction adapter for the existing web repository statements.

No SQL or scientific logic is executed remotely. Only the SELECT/INSERT/UPDATE
shapes used by Service are supported. Unsupported expressions fail closed.
Reads precede buffered writes; commit is atomic and never replays approval IO.
Pinned firestore SDK exposes the transaction lifecycle used here.
"""
import copy
from datetime import date
import hashlib
import json

from sqlalchemy import Date, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import operators
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import BindParameter, BinaryExpression, BooleanClauseList, Grouping, Null
from sqlalchemy.sql.selectable import Select


def value(expr, row):
    if isinstance(expr, BindParameter):return expr.value
    if isinstance(expr, Null):return None
    if isinstance(expr, Grouping):return value(expr.element,row)
    if isinstance(expr, BooleanClauseList):
        if expr.operator is operators.and_:return all(value(x,row)for x in expr.clauses)
        if expr.operator is operators.or_:return any(value(x,row)for x in expr.clauses)
    if isinstance(expr, BinaryExpression):
        left,right=value(expr.left,row),value(expr.right,row)
        op=expr.operator
        if op is operators.eq:return left==right
        if op is operators.ne:return left!=right
        if op is operators.in_op:return left in right
        if op is operators.is_:return left is right
        if op is operators.is_not:return left is not right
        if op is operators.json_getitem_op:return left.get(right) if isinstance(left,dict) else left[right]
    if getattr(expr,'table',None) is not None and getattr(expr,'name',None) in row:return row[expr.name]
    raise ValueError('Unsupported Firestore repository expression')


def equal_filter(clauses,name):
    for clause in clauses:
        if isinstance(clause,Grouping):clause=clause.element
        if isinstance(clause,BooleanClauseList):
            found=equal_filter(clause.clauses,name)
            if found is not None:return found
        if isinstance(clause,BinaryExpression)and clause.operator is operators.eq and getattr(clause.left,'name',None)==name and isinstance(clause.right,BindParameter):return clause.right.value
    return None


class Result:
    def __init__(self,rows=(),rowcount=0):self.rows=list(rows);self.rowcount=rowcount
    def mappings(self):return self
    def first(self):return self.rows[0]if self.rows else None
    def __iter__(self):return iter(self.rows)


class FirestoreEngine:
    def __init__(self,settings):
        from google.cloud import firestore
        self.client=firestore.Client(project=settings.firestore_project,database=settings.firestore_database)
        self.prefix=settings.firestore_prefix
        self.tables={}
    def ref(self,table,id):return self.client.collection(self.prefix+'_'+table).document(str(id))
    def connect(self):return Connection(self,False)
    def begin(self):return Connection(self,True)
    def dispose(self):self.client.close()


class Connection:
    def __init__(self,engine,transactional):
        self.engine=engine;self.transactional=transactional;self.transaction=None;self.cache={};self.writes={}
    def __enter__(self):
        if self.transactional:
            self.transaction=self.engine.client.transaction(max_attempts=1)
            self.transaction._begin()
            guard=getattr(self.engine,'authorization_guard',None)
            if guard is not None:
                try:guard(self.transaction)
                except BaseException:
                    if self.transaction.in_progress:self.transaction._rollback()
                    raise
        return self
    def __exit__(self,kind,exception,traceback):
        try:
            if kind is None and self.transaction is not None:
                for (table,id),row in self.writes.items():
                    if row is None:self.transaction.delete(self.engine.ref(table,id))
                    else:self.transaction.set(self.engine.ref(table,id),row)
                try:self.transaction._commit()
                except Exception as exc:
                    from google.api_core.exceptions import Aborted
                    if isinstance(exc,Aborted):
                        from .service import Conflict
                        raise Conflict('Concurrent record update; reload and retry')from exc
                    raise
        finally:
            if self.transaction is not None and self.transaction.in_progress:
                try:self.transaction._rollback()
                except Exception:pass
    def exec_driver_sql(self,statement):
        if statement!='SELECT 1':raise ValueError('Unsupported health statement')
        self.engine.ref('health','probe').get(timeout=10)
        return Result([{'health':1}])
    def raw(self,table,id):
        key=(table,str(id))
        if key not in self.cache:
            ref=self.engine.ref(table,id)
            try:snap=ref.get(transaction=self.transaction,timeout=20)
            except Exception as exc:
                from google.api_core.exceptions import Aborted
                if isinstance(exc,Aborted):
                    from .service import Conflict
                    raise Conflict('Concurrent record update; reload and retry')from exc
                raise
            self.cache[key]=snap.to_dict()if snap.exists else None
        return copy.deepcopy(self.cache[key])
    def decode(self,table,raw):
        if raw is None:return None
        row={k:v for k,v in raw.items()if k!='payload_json'}
        row['payload']=json.loads(raw['payload_json'])if 'payload_json'in raw else {}
        for column in table.c:
            if isinstance(column.type,Date)and isinstance(row.get(column.name),str):row[column.name]=date.fromisoformat(row[column.name])
        return row
    def encode(self,table,row):
        raw={k:(v.isoformat()if isinstance(v,date)else v)for k,v in row.items()if k!='payload'}
        raw['payload_json']=json.dumps(row.get('payload',{}),allow_nan=False,separators=(',',':'))
        if len(json.dumps(raw,allow_nan=False).encode())>900_000:raise ValueError('Metadata exceeds safe Firestore document size')
        return raw
    def matching(self,table,clauses):
        id=equal_filter(clauses,'id')
        if id is not None:
            row=self.decode(table,self.raw(table.name,id))
            return [row]if row is not None and all(value(c,row)for c in clauses)else[]
        if self.transactional:raise ValueError('Transactional repository reads require a document ID')
        from google.cloud.firestore_v1.base_query import FieldFilter
        query=self.engine.client.collection(self.engine.prefix+'_'+table.name)
        profile=equal_filter(clauses,'profile_id')
        if profile is not None:query=query.where(filter=FieldFilter('profile_id','==',profile))
        rows=[self.decode(table,s.to_dict())for s in query.stream(timeout=20)]
        return[r for r in rows if all(value(c,r)for c in clauses)]
    def reserve(self,table,row):
        if self.raw(table.name,row['id'])is not None:raise IntegrityError(None,None,ValueError('Duplicate ID'))
        for column in table.c:
            v=row.get(column.name)
            if not column.nullable and v is None:raise IntegrityError(None,None,ValueError('Required field missing'))
            for fk in column.foreign_keys:
                if v is not None and self.raw(fk.column.table.name,v)is None:raise IntegrityError(None,None,ValueError('Missing referenced record'))
        for constraint in table.constraints:
            if isinstance(constraint,UniqueConstraint):
                names=[c.name for c in constraint.columns]
                vals=[row.get(n)for n in names]
                if any(v is None for v in vals):continue
                text=json.dumps([table.name,names,vals],default=lambda v:v.isoformat(),separators=(',',':'))
                id=hashlib.sha256(text.encode()).hexdigest();key=('uniques',id)
                if self.raw(*key)is not None:raise IntegrityError(None,None,ValueError('Duplicate unique reservation'))
                self.cache[key]=self.writes[key]={'table':table.name,'record_id':row['id']}
    def execute(self,statement):
        if isinstance(statement,Select):
            tables=statement.get_final_froms()
            if len(tables)!=1:raise ValueError('Unsupported repository SELECT')
            return Result(self.matching(tables[0],statement._where_criteria))
        if not self.transactional:raise ValueError('Repository writes require a transaction')
        table=statement.table
        if isinstance(statement,Delete):
            # No scientific table is deletable through this adapter. Sessions
            # have no dependants or uniqueness reservations; delete by ID only.
            if table.name!='review_sessions' or equal_filter(statement._where_criteria,'id') is None:
                raise ValueError('Only identified review sessions may be deleted')
            rows=self.matching(table,statement._where_criteria)
            for row in rows:
                key=(table.name,str(row['id']));self.cache[key]=self.writes[key]=None
            return Result(rowcount=len(rows))
        fields={getattr(k,'name',k):value(v,{})for k,v in statement._values.items()}
        if isinstance(statement,Insert):
            row={c.name:fields.get(c.name)for c in table.c};self.reserve(table,row);rows=[row]
        elif isinstance(statement,Update):
            rows=self.matching(table,statement._where_criteria)
            rows=[{**r,**fields}for r in rows]
            unique_names={'id'}|{c.name for constraint in table.constraints if isinstance(constraint,UniqueConstraint) for c in constraint.columns}
            if unique_names.intersection(fields):raise ValueError('Unique-key updates are not supported')
            for row in rows:
                for column in table.c:
                    for fk in column.foreign_keys:
                        v=row.get(column.name)
                        if v is not None and self.raw(fk.column.table.name,v)is None:raise IntegrityError(None,None,ValueError('Missing referenced record'))
        else:raise ValueError('Unsupported repository mutation')
        for row in rows:
            key=(table.name,str(row['id']));raw=self.encode(table,row);self.cache[key]=raw;self.writes[key]=raw
        return Result(rowcount=len(rows))
