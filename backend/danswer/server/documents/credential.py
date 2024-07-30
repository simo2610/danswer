from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from danswer.auth.schemas import UserRole
from danswer.auth.users import current_admin_user
from danswer.auth.users import current_user
from danswer.db.credentials import alter_credential
from danswer.db.credentials import create_credential
from danswer.db.credentials import delete_credential
from danswer.db.credentials import fetch_credential_by_id
from danswer.db.credentials import fetch_credentials
from danswer.db.credentials import fetch_credentials_by_source
from danswer.db.credentials import swap_credentials_connector
from danswer.db.credentials import update_credential
from danswer.db.engine import get_session
from danswer.db.models import DocumentSource
from danswer.db.models import User
from danswer.server.documents.models import CredentialBase
from danswer.server.documents.models import CredentialDataUpdateRequest
from danswer.server.documents.models import CredentialSnapshot
from danswer.server.documents.models import CredentialSwapRequest
from danswer.server.documents.models import ObjectCreationIdResponse
from danswer.server.models import StatusResponse


router = APIRouter(prefix="/manage")


"""Admin-only endpoints"""


@router.get("/admin/credential")
def list_credentials_admin(
    user: User = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> list[CredentialSnapshot]:
    """Lists all public credentials"""
    credentials = fetch_credentials(db_session=db_session, user=user)
    return [
        CredentialSnapshot.from_credential_db_model(credential)
        for credential in credentials
    ]


@router.get("/admin/similar-credentials/{source_type}")
def get_cc_source_full_info(
    source_type: DocumentSource,
    user: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> list[CredentialSnapshot]:
    credentials = fetch_credentials_by_source(
        db_session=db_session, user=user, document_source=source_type
    )

    return [
        CredentialSnapshot.from_credential_db_model(credential)
        for credential in credentials
    ]


@router.get("/credentials/{id}")
def list_credentials_by_id(
    user: User | None = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> list[CredentialSnapshot]:
    credentials = fetch_credentials(db_session=db_session, user=user)
    return [
        CredentialSnapshot.from_credential_db_model(credential)
        for credential in credentials
    ]


@router.delete("/admin/credential/{credential_id}")
def delete_credential_by_id_admin(
    credential_id: int,
    _: User = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    """Same as the user endpoint, but can delete any credential (not just the user's own)"""
    delete_credential(db_session=db_session, credential_id=credential_id, user=None)
    return StatusResponse(
        success=True, message="Credential deleted successfully", data=credential_id
    )


@router.put("/admin/credentials/swap")
def swap_credentials_for_connector(
    credentail_swap_req: CredentialSwapRequest,
    user: User | None = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    connector_credential_pair = swap_credentials_connector(
        new_credential_id=credentail_swap_req.new_credential_id,
        connector_id=credentail_swap_req.connector_id,
        db_session=db_session,
        user=user,
    )

    return StatusResponse(
        success=True,
        message="Credential swapped successfully",
        data=connector_credential_pair.id,
    )


"""Endpoints for all"""


@router.get("/credential")
def list_credentials(
    user: User | None = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> list[CredentialSnapshot]:
    credentials = fetch_credentials(db_session=db_session, user=user)
    return [
        CredentialSnapshot.from_credential_db_model(credential)
        for credential in credentials
    ]


@router.post("/credential")
def create_credential_from_model(
    credential_info: CredentialBase,
    user: User | None = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> ObjectCreationIdResponse:
    if user and user.role != UserRole.ADMIN and credential_info.admin_public:
        raise HTTPException(
            status_code=400,
            detail="Non-admin cannot create admin credential",
        )

    credential = create_credential(credential_info, user, db_session)

    return ObjectCreationIdResponse(
        id=credential.id,
        credential=CredentialSnapshot.from_credential_db_model(credential),
    )


@router.get("/credential/{credential_id}")
def get_credential_by_id(
    credential_id: int,
    user: User = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> CredentialSnapshot | StatusResponse[int]:
    credential = fetch_credential_by_id(credential_id, user, db_session)
    if credential is None:
        raise HTTPException(
            status_code=401,
            detail=f"Credential {credential_id} does not exist or does not belong to user",
        )

    return CredentialSnapshot.from_credential_db_model(credential)


@router.put("/admin/credentials/{credential_id}")
def update_credential_data(
    credential_id: int,
    credential_update: CredentialDataUpdateRequest,
    user: User = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> CredentialBase:
    credential = alter_credential(credential_id, credential_update, user, db_session)

    if credential is None:
        raise HTTPException(
            status_code=401,
            detail=f"Credential {credential_id} does not exist or does not belong to user",
        )

    return CredentialSnapshot.from_credential_db_model(credential)


@router.patch("/credential/{credential_id}")
def update_credential_from_model(
    credential_id: int,
    credential_data: CredentialBase,
    user: User = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> CredentialSnapshot | StatusResponse[int]:
    updated_credential = update_credential(
        credential_id, credential_data, user, db_session
    )
    if updated_credential is None:
        raise HTTPException(
            status_code=401,
            detail=f"Credential {credential_id} does not exist or does not belong to user",
        )

    return CredentialSnapshot(
        source=updated_credential.source,
        id=updated_credential.id,
        credential_json=updated_credential.credential_json,
        user_id=updated_credential.user_id,
        admin_public=updated_credential.admin_public,
        time_created=updated_credential.time_created,
        time_updated=updated_credential.time_updated,
    )


@router.delete("/credential/{credential_id}")
def delete_credential_by_id(
    credential_id: int,
    user: User = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    delete_credential(
        credential_id,
        user,
        db_session,
    )

    return StatusResponse(
        success=True, message="Credential deleted successfully", data=credential_id
    )


@router.delete("/credential/force/{credential_id}")
def force_delete_credential_by_id(
    credential_id: int,
    user: User = Depends(current_user),
    db_session: Session = Depends(get_session),
) -> StatusResponse:
    delete_credential(credential_id, user, db_session, True)

    return StatusResponse(
        success=True, message="Credential deleted successfully", data=credential_id
    )
