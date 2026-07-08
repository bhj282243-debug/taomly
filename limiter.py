"""
limiter.py — slowapi Limiter singleton.
Выделен из api.py чтобы избежать циклических импортов.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
