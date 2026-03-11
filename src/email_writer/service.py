"""
Email Writer service — generates emails using OpenAI.
Stateless, user-level (no history stored).
"""
import logging
from openai import AsyncOpenAI

from src.config import settings
from src.models.email import EmailGenerateRequest, EmailTone, EmailType

logger = logging.getLogger(__name__)

# Map enums to human-readable descriptions for the prompt
TONE_DESCRIPTIONS: dict[EmailTone, str] = {
    EmailTone.PROFESSIONAL: "professional and business-appropriate",
    EmailTone.FRIENDLY: "warm and friendly while remaining respectful",
    EmailTone.FORMAL: "highly formal with proper etiquette",
    EmailTone.CASUAL: "relaxed and conversational",
    EmailTone.PERSUASIVE: "compelling and persuasive",
    EmailTone.APOLOGETIC: "sincere and apologetic",
}

EMAIL_TYPE_DESCRIPTIONS: dict[EmailType, str] = {
    EmailType.NEW: "a brand-new email",
    EmailType.REPLY: "a reply to a previous email",
    EmailType.FOLLOW_UP: "a follow-up on a previous conversation or meeting",
    EmailType.THANK_YOU: "a thank-you email",
    EmailType.INTRODUCTION: "an introductory/self-introduction email",
    EmailType.REQUEST: "an email making a specific request",
}

SYSTEM_PROMPT = """You are an expert email writer. Your job is to draft clear, well-structured emails based on the user's key points and preferences.

Rules:
- Write ONLY the email body (greeting through sign-off). Do NOT include "Subject:" lines or metadata.
- Use appropriate greetings and sign-offs based on the tone.
- Keep paragraphs concise and scannable.
- Preserve all specific details, names, dates, and numbers the user provides.
- Do not add information the user did not mention.
- Match the requested tone precisely.
- If a subject line is provided, make the email content consistent with it.
- End with an appropriate sign-off (e.g., "Best regards," / "Thanks," / "Sincerely,") followed by a placeholder like [Your Name]."""


class EmailWriterService:
    """Generates emails via OpenAI chat completions."""

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    async def generate_email(self, request: EmailGenerateRequest) -> str:
        """
        Generate an email from the user's input.

        Args:
            request: The email generation request with key points, tone, type, etc.

        Returns:
            The generated email text.
        """
        tone_desc = TONE_DESCRIPTIONS.get(request.tone, "professional")
        type_desc = EMAIL_TYPE_DESCRIPTIONS.get(request.email_type, "a new email")

        user_message_parts = [
            f"Write {type_desc} with a {tone_desc} tone.",
        ]

        if request.subject:
            user_message_parts.append(f"Subject: {request.subject}")

        if request.recipient_context:
            user_message_parts.append(f"Recipient: {request.recipient_context}")

        user_message_parts.append(f"Key points to cover:\n{request.key_points}")

        user_message = "\n\n".join(user_message_parts)

        logger.info("Generating email — type=%s, tone=%s", request.email_type, request.tone)

        response = await self.client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=settings.OPENAI_CHAT_MAX_TOKENS,
            temperature=0.7,
        )

        generated = response.choices[0].message.content or ""
        return generated.strip()


# Module-level singleton
email_writer_service = EmailWriterService()
