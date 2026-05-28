import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

bearer = HTTPBearer(auto_error=False)


async def get_current_user_id(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    # TODO(EP-01): verify against Supabase JWKS for prod (rotating keys);
    # for now decode with the project's shared JWT secret (HS256).
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing sub")
    return str(user_id)
