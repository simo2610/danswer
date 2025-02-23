import stripe
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ee.onyx.auth.users import current_cloud_superuser
from ee.onyx.auth.users import generate_anonymous_user_jwt_token
from ee.onyx.configs.app_configs import ANONYMOUS_USER_COOKIE_NAME
from ee.onyx.configs.app_configs import STRIPE_SECRET_KEY
from ee.onyx.server.tenants.access import control_plane_dep
from ee.onyx.server.tenants.anonymous_user_path import get_anonymous_user_path
from ee.onyx.server.tenants.anonymous_user_path import (
    get_tenant_id_for_anonymous_user_path,
)
from ee.onyx.server.tenants.anonymous_user_path import modify_anonymous_user_path
from ee.onyx.server.tenants.anonymous_user_path import validate_anonymous_user_path
from ee.onyx.server.tenants.billing import fetch_billing_information
from ee.onyx.server.tenants.billing import fetch_stripe_checkout_session
from ee.onyx.server.tenants.billing import fetch_tenant_stripe_information
from ee.onyx.server.tenants.models import AnonymousUserPath
from ee.onyx.server.tenants.models import BillingInformation
from ee.onyx.server.tenants.models import ImpersonateRequest
from ee.onyx.server.tenants.models import ProductGatingRequest
from ee.onyx.server.tenants.models import ProductGatingResponse
from ee.onyx.server.tenants.models import SubscriptionSessionResponse
from ee.onyx.server.tenants.models import SubscriptionStatusResponse
from ee.onyx.server.tenants.product_gating import store_product_gating
from ee.onyx.server.tenants.provisioning import delete_user_from_control_plane
from ee.onyx.server.tenants.user_mapping import get_tenant_id_for_email
from ee.onyx.server.tenants.user_mapping import remove_all_users_from_tenant
from ee.onyx.server.tenants.user_mapping import remove_users_from_tenant
from onyx.auth.users import anonymous_user_enabled
from onyx.auth.users import auth_backend
from onyx.auth.users import current_admin_user
from onyx.auth.users import get_redis_strategy
from onyx.auth.users import optional_user
from onyx.auth.users import User
from onyx.configs.app_configs import WEB_DOMAIN
from onyx.configs.constants import FASTAPI_USERS_AUTH_COOKIE_NAME
from onyx.db.auth import get_user_count
from onyx.db.engine import get_session
from onyx.db.engine import get_session_with_shared_schema
from onyx.db.engine import get_session_with_tenant
from onyx.db.users import delete_user_from_db
from onyx.db.users import get_user_by_email
from onyx.server.manage.models import UserByEmail
from onyx.utils.logger import setup_logger
from shared_configs.contextvars import CURRENT_TENANT_ID_CONTEXTVAR
from shared_configs.contextvars import get_current_tenant_id

stripe.api_key = STRIPE_SECRET_KEY
logger = setup_logger()
router = APIRouter(prefix="/tenants")


@router.get("/anonymous-user-path")
async def get_anonymous_user_path_api(
    _: User | None = Depends(current_admin_user),
) -> AnonymousUserPath:
    tenant_id = get_current_tenant_id()

    if tenant_id is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    with get_session_with_shared_schema() as db_session:
        current_path = get_anonymous_user_path(tenant_id, db_session)

    return AnonymousUserPath(anonymous_user_path=current_path)


@router.post("/anonymous-user-path")
async def set_anonymous_user_path_api(
    anonymous_user_path: str,
    _: User | None = Depends(current_admin_user),
) -> None:
    tenant_id = get_current_tenant_id()
    try:
        validate_anonymous_user_path(anonymous_user_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    with get_session_with_shared_schema() as db_session:
        try:
            modify_anonymous_user_path(tenant_id, anonymous_user_path, db_session)
        except IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="The anonymous user path is already in use. Please choose a different path.",
            )
        except Exception as e:
            logger.exception(f"Failed to modify anonymous user path: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="An unexpected error occurred while modifying the anonymous user path",
            )


@router.post("/anonymous-user")
async def login_as_anonymous_user(
    anonymous_user_path: str,
    _: User | None = Depends(optional_user),
) -> Response:
    with get_session_with_shared_schema() as db_session:
        tenant_id = get_tenant_id_for_anonymous_user_path(
            anonymous_user_path, db_session
        )
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

    if not anonymous_user_enabled(tenant_id=tenant_id):
        raise HTTPException(status_code=403, detail="Anonymous user is not enabled")

    token = generate_anonymous_user_jwt_token(tenant_id)

    response = Response()
    response.delete_cookie(FASTAPI_USERS_AUTH_COOKIE_NAME)
    response.set_cookie(
        key=ANONYMOUS_USER_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return response


@router.post("/product-gating")
def gate_product(
    product_gating_request: ProductGatingRequest, _: None = Depends(control_plane_dep)
) -> ProductGatingResponse:
    """
    Gating the product means that the product is not available to the tenant.
    They will be directed to the billing page.
    We gate the product when their subscription has ended.
    """
    try:
        store_product_gating(
            product_gating_request.tenant_id, product_gating_request.application_status
        )
        return ProductGatingResponse(updated=True, error=None)

    except Exception as e:
        logger.exception("Failed to gate product")
        return ProductGatingResponse(updated=False, error=str(e))


@router.get("/billing-information")
async def billing_information(
    _: User = Depends(current_admin_user),
) -> BillingInformation | SubscriptionStatusResponse:
    logger.info("Fetching billing information")
    tenant_id = get_current_tenant_id()
    return fetch_billing_information(tenant_id)


@router.post("/create-customer-portal-session")
async def create_customer_portal_session(
    _: User = Depends(current_admin_user),
) -> dict:
    tenant_id = get_current_tenant_id()

    try:
        stripe_info = fetch_tenant_stripe_information(tenant_id)
        stripe_customer_id = stripe_info.get("stripe_customer_id")
        if not stripe_customer_id:
            raise HTTPException(status_code=400, detail="Stripe customer ID not found")
        logger.info(stripe_customer_id)

        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{WEB_DOMAIN}/admin/billing",
        )
        logger.info(portal_session)
        return {"url": portal_session.url}
    except Exception as e:
        logger.exception("Failed to create customer portal session")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create-subscription-session")
async def create_subscription_session(
    _: User = Depends(current_admin_user),
) -> SubscriptionSessionResponse:
    try:
        tenant_id = CURRENT_TENANT_ID_CONTEXTVAR.get()
        if not tenant_id:
            raise HTTPException(status_code=400, detail="Tenant ID not found")
        session_id = fetch_stripe_checkout_session(tenant_id)
        return SubscriptionSessionResponse(sessionId=session_id)

    except Exception as e:
        logger.exception("Failed to create resubscription session")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/impersonate")
async def impersonate_user(
    impersonate_request: ImpersonateRequest,
    _: User = Depends(current_cloud_superuser),
) -> Response:
    """Allows a cloud superuser to impersonate another user by generating an impersonation JWT token"""
    tenant_id = get_tenant_id_for_email(impersonate_request.email)

    with get_session_with_tenant(tenant_id=tenant_id) as tenant_session:
        user_to_impersonate = get_user_by_email(
            impersonate_request.email, tenant_session
        )
        if user_to_impersonate is None:
            raise HTTPException(status_code=404, detail="User not found")
        token = await get_redis_strategy().write_token(user_to_impersonate)

    response = await auth_backend.transport.get_login_response(token)
    response.set_cookie(
        key="fastapiusersauth",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.post("/leave-organization")
async def leave_organization(
    user_email: UserByEmail,
    current_user: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> None:
    tenant_id = get_current_tenant_id()

    if current_user is None or current_user.email != user_email.user_email:
        raise HTTPException(
            status_code=403, detail="You can only leave the organization as yourself"
        )

    user_to_delete = get_user_by_email(user_email.user_email, db_session)
    if user_to_delete is None:
        raise HTTPException(status_code=404, detail="User not found")

    num_admin_users = await get_user_count(only_admin_users=True)

    should_delete_tenant = num_admin_users == 1

    if should_delete_tenant:
        logger.info(
            "Last admin user is leaving the organization. Deleting tenant from control plane."
        )
        try:
            await delete_user_from_control_plane(tenant_id, user_to_delete.email)
            logger.debug("User deleted from control plane")
        except Exception as e:
            logger.exception(
                f"Failed to delete user from control plane for tenant {tenant_id}: {e}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to remove user from control plane: {str(e)}",
            )

    db_session.expunge(user_to_delete)
    delete_user_from_db(user_to_delete, db_session)

    if should_delete_tenant:
        remove_all_users_from_tenant(tenant_id)
    else:
        remove_users_from_tenant([user_to_delete.email], tenant_id)
