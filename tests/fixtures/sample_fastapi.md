# Security

FastAPI provides several security utilities.

## OAuth2PasswordBearer

```python
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
```

`OAuth2PasswordBearer` is a class that implements OAuth2 with Password flow using Bearer tokens.

### Parameters

- `tokenUrl` (str): The URL to obtain the token.
- `scheme_name` (str, optional): Override the scheme name.
- `scopes` (dict, optional): OAuth2 scopes.
- `auto_error` (bool): If True, returns 401 on missing token. Default: True.

### Usage Example

```python
from fastapi import FastAPI, Depends
from fastapi.security import OAuth2PasswordBearer

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/users/me")
async def read_users_me(token: str = Depends(oauth2_scheme)):
    return {"token": token}
```

## HTTPBearer

A simpler bearer token scheme.

```python
from fastapi.security import HTTPBearer

security = HTTPBearer()
```

## Important Notes

!!! warning
    `Depends` caches per-request, not globally. If you need global caching, use a separate mechanism.

!!! note "Breaking Change in v0.100"
    The `security` module was restructured. Import paths changed.
