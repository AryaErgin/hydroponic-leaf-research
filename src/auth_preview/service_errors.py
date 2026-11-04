from fastapi import HTTPException


def unavailable(message):
    raise HTTPException(503,message)
