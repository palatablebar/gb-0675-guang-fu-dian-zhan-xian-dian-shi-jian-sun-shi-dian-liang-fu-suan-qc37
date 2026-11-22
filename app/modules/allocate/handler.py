from fastapi import APIRouter, Request, Body
from app.engine import evaluate
from app.web import render
router=APIRouter()
@router.get('/allocate')
def page(request:Request):
    return render(request,'allocate')
@router.post('/api/allocate')
def calculate(payload:dict=Body(default={})):
    return evaluate(payload,until=5)
