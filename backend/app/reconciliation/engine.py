"""Bounded deterministic comparison; no SQL/Python from users is executed."""
import json
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from datetime import datetime
from fastapi import HTTPException

def key(row,columns,normalize_types=False,case_sensitive=True):
    values=[row.get(c) for c in columns]
    if any(v is None for v in values):return None
    normalized=[]
    for value in values:
        if isinstance(value,str) and not case_sensitive:value=value.casefold()
        if normalize_types and not isinstance(value,bool):
            import re
            if isinstance(value,(int,float,Decimal)) or (isinstance(value,str) and re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value)):
                try:
                    number=Decimal(str(value))
                    if number.is_finite():value=['number',str(number.normalize())]
                except InvalidOperation:pass
        normalized.append(value)
    return json.dumps(normalized,sort_keys=True,default=str,separators=(',',':'))

def filtered(rows,filters):
    def match(row,f):
        if f.column not in row:raise HTTPException(422,'Filter column is absent: '+f.column)
        left,right=row[f.column],f.value
        if isinstance(left,(int,float,Decimal)) and not isinstance(left,bool) and isinstance(right,str):
            try:right=Decimal(right)
            except InvalidOperation:raise HTTPException(422,'Numeric filter needs a numeric value') from None
        if isinstance(left,bool) and isinstance(right,str):
            if right.lower() not in ['true','false']:raise HTTPException(422,'Boolean filter needs true or false')
            right=right.lower()=='true'
        if f.operator=='not_null':return left is not None
        if f.operator=='eq':return left==right
        if f.operator=='ne':return left!=right
        if left is None:return False
        try:
            return {'gt':lambda:left>right,'gte':lambda:left>=right,'lt':lambda:left<right,'lte':lambda:left<=right}[f.operator]()
        except TypeError:raise HTTPException(422,'Filter values have incompatible types') from None
    return [row for row in rows if all(match(row,f) for f in filters)]

def joined(datasets,joins,max_rows):
    output=datasets[0]
    for dataset,condition in zip(datasets[1:],joins):
        index=defaultdict(list)
        if dataset and condition.right not in dataset[0]:raise HTTPException(422,'Join right column is absent')
        if output and condition.left not in output[0]:raise HTTPException(422,'Join left column is absent')
        for row in dataset:
            if row.get(condition.right) is not None:index[json.dumps(row[condition.right],default=str)].append(row)
        rows=[]
        for left in output:
            matches=index.get(json.dumps(left.get(condition.left),default=str),[]) if left.get(condition.left) is not None else []
            # Left join retains source records whose related entity is absent.
            empty={k:None for row in dataset[:1] for k in row if k!='__ids'}
            for right in matches or [empty]:
                rows.append({**left,**{k:v for k,v in right.items() if k!='__ids'},'__ids':left['__ids']|right.get('__ids',set())})
                if len(rows)>max_rows:raise HTTPException(422,'Join exceeds the bounded execution limit')
        output=rows
    return output

def transform(value,operation):
    if value is None:return None
    if operation=='exact':return value
    if not isinstance(value,str):raise HTTPException(422,'Text transformation received a non-text value')
    return {'trim':str.strip,'lower':str.lower,'upper':str.upper}[operation](value)

def prepare(source,branch):
    source=filtered(source,branch.source_filters)
    rows=[]
    for row in source:
        missing=[c.source for c in branch.columns if c.source not in row]
        if missing:raise HTTPException(422,'Mapped source column is absent: '+missing[0])
        rows.append({**{c.target:transform(row[c.source],c.transformation) for c in branch.columns},'__ids':row['__ids'],'__raw':row})
    if not branch.aggregations:return [{k:v for k,v in row.items() if k!='__raw'} for row in rows]
    groups=defaultdict(list)
    for row in rows:
        group=key(row,branch.business_keys)
        if group is None:raise HTTPException(422,'Aggregation group keys cannot be null')
        groups[group].append(row)
    output=[]
    for group in groups.values():
        item={c:group[0][c] for c in branch.business_keys};item['__ids']=set().union(*(row['__ids'] for row in group))
        for agg in branch.aggregations:
            if any(agg.source not in r['__raw'] for r in group):raise HTTPException(422,'Aggregation source column is absent')
            values=[r['__raw'][agg.source] for r in group if r['__raw'][agg.source] is not None]
            try:
                if agg.function=='COUNT':result=len(values)
                elif not values:result=None
                elif agg.function in ['SUM','AVG']:
                    nums=[Decimal(str(v)) for v in values]
                    if not all(n.is_finite() for n in nums):raise ValueError()
                    result=sum(nums)/(len(nums) if agg.function=='AVG' else 1)
                elif agg.function=='MIN':result=min(values)
                else:result=max(values)
            except (TypeError,ValueError,InvalidOperation):raise HTTPException(422,'Aggregation values have incompatible types') from None
            item[agg.target]=result
        output.append(item)
    return output

def equal(a,b,branch):
    if a is None or b is None:return branch.nulls_equal and a is None and b is None
    if isinstance(a,bool) or isinstance(b,bool):return type(a)==type(b) and a==b
    try:
        x,y=Decimal(str(a)),Decimal(str(b))
        if x.is_finite() and y.is_finite():return abs(x-y)<=Decimal(str(branch.numeric_tolerance))
    except (ValueError,InvalidOperation):pass
    if isinstance(a,(str,datetime)) and isinstance(b,(str,datetime)):
        try:
            x=datetime.fromisoformat(str(a).replace('Z','+00:00'));y=datetime.fromisoformat(str(b).replace('Z','+00:00'))
            return abs((x-y).total_seconds())<=branch.timestamp_tolerance_seconds
        except (ValueError,TypeError):pass
    if isinstance(a,str) and isinstance(b,str) and not branch.case_sensitive:return a.casefold()==b.casefold()
    return a==b

def compare(source,target,branch):
    source=prepare(source,branch);target=filtered(target,branch.target_filters)
    for row in target:
        if any(c not in row for c in branch.business_keys):raise HTTPException(422,'Target business key column is absent')
    source_index=defaultdict(list);target_index=defaultdict(list)
    for i,row in enumerate(source):source_index[key(row,branch.business_keys,branch.normalize_types,branch.case_sensitive) or 'null-source-'+str(i)].append(row)
    for i,row in enumerate(target):target_index[key(row,branch.business_keys,branch.normalize_types,branch.case_sensitive) or 'null-target-'+str(i)].append(row)
    stats={'source_count':len(source),'target_count':len(target),'matched':0,'mismatched':0,'missing':0,'extra':0,'duplicates':0}
    differences=[];relevant=set();failed=set()
    for row in source:relevant|=row['__ids']
    def clean(row):return {k:v for k,v in row.items() if not k.startswith('__')}
    def difference(kind,s,t,column=None):
        original=s or t
        differences.append({'difference_type':kind,'business_key':{k:original.get(k) for k in branch.business_keys},'column_name':column,
            'source_value':{'record':clean(s)} if s else None,'target_value':{'record':clean(t)} if t else None})
        if len(differences)>10000:raise HTTPException(422,'More than 10,000 detailed differences in this branch. Compare fewer columns or a filtered dataset.')
    for identity in sorted(set(source_index)|set(target_index)):
        left,right=source_index.get(identity,[]),target_index.get(identity,[])
        if len(left)>1 or len(right)>1:
            stats['duplicates']+=max(len(left)-1,0)+max(len(right)-1,0)
            stats['mismatched']+=len(left)
            for row in left:failed|=row['__ids']
            difference('duplicate',left[0] if left else None,right[0] if right else None);continue
        if not right:
            stats['missing']+=1;failed|=left[0]['__ids'];difference('missing',left[0],None);continue
        if not left:
            stats['extra']+=1;difference('extra',None,right[0]);continue
        s,t=left[0],right[0];mismatches=[]
        for column in [c.target for c in branch.columns]+[a.target for a in branch.aggregations]:
            if column not in t or not equal(s.get(column),t.get(column),branch):mismatches.append(column)
        for column in branch.required_columns:
            if s.get(column) is None or t.get(column) is None:
                if column not in mismatches:mismatches.append(column)
        if mismatches:
            stats['mismatched']+=1;failed|=s['__ids']
            for column in mismatches:difference('mismatched',s,t,column)
        else:stats['matched']+=1
    stats['match_percentage']=round(stats['matched']/stats['source_count']*100,4) if stats['source_count'] else None
    stats['rule_results']=[{'rule':'record_count','source':len(source),'target':len(target),'passed':len(source)==len(target)},
        {'rule':'schema','missing_target_columns':sorted(set(c.target for c in branch.columns)-set(target[0] if target else {}))}]
    return stats,differences,relevant,failed
