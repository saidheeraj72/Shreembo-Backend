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
from src.email_agent import issues as issue_builder
from src.email_agent import mind_map as mind_map_builder
from src.email_agent.agent import email_agent
from src.models.email_agent import (
    EmailAccountResponse,
    EmailAgentChatRequest,
    EmailAgentChatResponse,
    EmailDetail,
    EmailListItem,
    EmailListResponse,
    IssueTableRequest,
    IssueTableResponse,
    MindMapRequest,
    MindMapResponse,
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
    max_results: int = Query(25, ge=1, le=100),
    page_token: str | None = Query(None, description="Token from a previous response's next_page_token"),
    user_id: UUID = Depends(get_current_user_id),
) -> EmailListResponse:
    account = await account_store.get_account(user_id, account_id)
    access_token = await account_store.get_valid_access_token(account)
    result = await gmail_client.list_messages(
        access_token, query=query, max_results=max_results, page_token=page_token
    )
    return EmailListResponse(
        account_id=account_id,
        items=[EmailListItem(**m) for m in result["messages"]],
        next_page_token=result["next_page_token"],
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


@router.post(
    "/accounts/{account_id}/mind-map",
    response_model=MindMapResponse,
    summary="Build a problem/issue mind map from the mailbox",
)
async def build_mind_map(
    account_id: UUID,
    request: MindMapRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> MindMapResponse:
    account = await account_store.get_account(user_id, account_id)
    graph = await mind_map_builder.build_mind_map(
        account,
        query=request.query,
        max_emails=request.max_emails,
        map_type=request.map_type,
    )
    return MindMapResponse(
        account_id=account_id,
        nodes=graph["nodes"],
        edges=graph["edges"],
        email_count=graph["email_count"],
        generated_at=graph["generated_at"],
    )


@router.post(
    "/accounts/{account_id}/issues",
    response_model=IssueTableResponse,
    summary="Build a table of recurring issues from the mailbox",
)
async def build_issue_table(
    account_id: UUID,
    request: IssueTableRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> IssueTableResponse:
    account = await account_store.get_account(user_id, account_id)
    result = await issue_builder.build_issue_table(
        account,
        query=request.query,
        max_emails=request.max_emails,
    )
    # Cache the latest snapshot so the table loads instantly next time.
    issue_builder.save_scan(account_id, user_id, result, query=request.query)
    return IssueTableResponse(
        account_id=account_id,
        issues=result["issues"],
        email_count=result["email_count"],
        generated_at=result["generated_at"],
    )


@router.get(
    "/accounts/{account_id}/issues",
    response_model=IssueTableResponse | None,
    summary="Get the latest cached issues scan (null if never scanned)",
)
async def get_issue_table(
    account_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> IssueTableResponse | None:
    # Ensure the account belongs to the user before returning its cached scan.
    await account_store.get_account(user_id, account_id)
    snapshot = issue_builder.get_latest_scan(account_id, user_id)
    if not snapshot:
        return None
    return IssueTableResponse(
        account_id=account_id,
        issues=snapshot["issues"],
        email_count=snapshot["email_count"],
        generated_at=snapshot["generated_at"],
    )
