"""
Email Agent — mailbox operations over connected Gmail accounts.

  GET    /accounts                          list connected mailboxes
  DELETE /accounts/{account_id}             disconnect a mailbox
  GET    /accounts/{account_id}/messages    list/search emails  (?query=&max_results=)
  GET    /accounts/{account_id}/messages/{message_id}   get one email
  POST   /accounts/{account_id}/send        send an email
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.core.dependencies import get_current_user_id
from src.email_agent import accounts as account_store
from src.email_agent import gmail_client
from src.email_agent.agent import email_agent
from src.models.email_agent import (
    EmailAccountResponse,
    EmailAgentChatRequest,
    EmailAgentChatResponse,
    EmailDetail,
    EmailListItem,
    EmailListResponse,
    SendEmailRequest,
    SendEmailResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/accounts",
    response_model=list[EmailAccountResponse],
    summary="List connected mailboxes",
)
async def list_connected_accounts(
    user_id: UUID = Depends(get_current_user_id),
) -> list[EmailAccountResponse]:
    rows = await account_store.list_accounts(user_id)
    return [EmailAccountResponse(**row) for row in rows]


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    summary="Disconnect a mailbox",
)
async def disconnect_account(
    account_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> None:
    await account_store.delete_account(user_id, account_id)


@router.get(
    "/accounts/{account_id}/messages",
    response_model=EmailListResponse,
    summary="List or search emails",
)
async def list_messages(
    account_id: UUID,
    query: str | None = Query(None, description="Gmail search query, e.g. 'from:boss is:unread'"),
    max_results: int = Query(20, ge=1, le=100),
    user_id: UUID = Depends(get_current_user_id),
) -> EmailListResponse:
    account = await account_store.get_account(user_id, account_id)
    access_token = await account_store.get_valid_access_token(account)
    messages = await gmail_client.list_messages(
        access_token, query=query, max_results=max_results
    )
    return EmailListResponse(
        account_id=account_id,
        items=[EmailListItem(**m) for m in messages],
    )


@router.get(
    "/accounts/{account_id}/messages/{message_id}",
    response_model=EmailDetail,
    summary="Get one email",
)
async def get_message(
    account_id: UUID,
    message_id: str,
    user_id: UUID = Depends(get_current_user_id),
) -> EmailDetail:
    account = await account_store.get_account(user_id, account_id)
    access_token = await account_store.get_valid_access_token(account)
    message = await gmail_client.get_message(access_token, message_id)
    return EmailDetail(**message)


@router.post(
    "/accounts/{account_id}/send",
    response_model=SendEmailResponse,
    summary="Send an email",
)
async def send_email(
    account_id: UUID,
    request: SendEmailRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> SendEmailResponse:
    account = await account_store.get_account(user_id, account_id)
    access_token = await account_store.get_valid_access_token(account)
    result = await gmail_client.send_message(
        access_token,
        sender=account["email_address"],
        to=str(request.to),
        subject=request.subject,
        body=request.body,
        cc=request.cc,
        bcc=request.bcc,
        in_reply_to=request.in_reply_to,
        thread_id=request.thread_id,
    )
    return SendEmailResponse(id=result.get("id"), thread_id=result.get("threadId"))


@router.post(
    "/accounts/{account_id}/chat",
    response_model=EmailAgentChatResponse,
    summary="Chat with the email assistant",
)
async def chat(
    account_id: UUID,
    request: EmailAgentChatRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> EmailAgentChatResponse:
    account = await account_store.get_account(user_id, account_id)
    result = await email_agent.run(
        account,
        [m.model_dump() for m in request.messages],
    )
    return EmailAgentChatResponse(reply=result["reply"], actions=result["actions"])
