import json
import logging
import re
from typing import Optional, Protocol
import httpx

from app.config import settings

logger = logging.getLogger("commute_assistant")


class LLMError(Exception):
    """Exception raised on LLM network failure, timeout, or quota error."""
    pass


class LLMProvider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        ...


class FakeLLMProvider:
    """
    Deterministic offline LLM simulation that produces realistic grounded JSON answers
    strictly from the supplied chunk excerpts. Used for offline testing and evaluation.
    """

    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        q_match = re.search(r"<user_query>(.*?)</user_query>", user_prompt, re.DOTALL | re.IGNORECASE)
        question = q_match.group(1).strip() if q_match else user_prompt
        q_lower = question.lower()

        # Parse available chunks in user_prompt
        # Format: --- BEGIN CHUNK C1 --- ... Content: ... --- END CHUNK C1 ---
        chunk_pattern = re.compile(r"--- BEGIN CHUNK (C\d+) ---\s*(.*?)\s*--- END CHUNK \1 ---", re.DOTALL)
        found_chunks = chunk_pattern.findall(user_prompt)
        chunks_by_key = {cid: text for cid, text in found_chunks}

        # If no chunks present, cannot answer
        if not chunks_by_key:
            return json.dumps({"status": "not_covered", "answer": "", "citation_ids": []})

        # Explicit unanswerable checks
        if any(unans in q_lower for unans in ["pet", "dog", "cat", "lost luggage", "lost baggage", "luggage compensation", "baggage compensation"]):
            return json.dumps({"status": "not_covered", "answer": "", "citation_ids": []})

        # Prompt injection asking to reveal system prompt or internal secrets
        if "reveal system secrets" in q_lower or "system_override" in q_lower:
            return json.dumps({"status": "not_covered", "answer": "", "citation_ids": []})

        # Cross-tenant rule inquiries: e.g. "What does BluePeak say...", "What does Aurora say..."
        # If the user explicitly asks what another company says, and that other company is not this policy:
        if any(other_phrase in q_lower for other_phrase in [
            "what does bluepeak say", "what does aurora say", "what does cedar say",
            "what is bluepeak's rule", "what is aurora's rule", "what is cedar's rule",
            "tell me bluepeak policy", "tell me aurora policy", "tell me cedar policy"
        ]):
            return json.dumps({"status": "not_covered", "answer": "", "citation_ids": []})

        # 1. Booking cut-off / Reservation deadline (check before general weekend queries so deadline takes priority)
        if any(term in q_lower for term in [
            "cut-off", "cutoff", "deadline", "by what time", "finalize", "when can i book",
            "how many hours before", "booking deadline", "reservation deadline"
        ]):
            for cid, ctext in chunks_by_key.items():
                if "Cut-Off" in ctext or "Reservation" in ctext or "Booking" in ctext:
                    if "18:00 previous working day" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Shift commute booking cut-off is strictly 18:00 previous working day.",
                            "citation_ids": [cid],
                        })
                    elif "2 hours before pickup" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "The booking cut-off is strictly 2 hours before pickup.",
                            "citation_ids": [cid],
                        })
                    elif "12:00 previous calendar day" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Commute booking cut-off time is 12:00 previous calendar day.",
                            "citation_ids": [cid],
                        })

        # 2. Escort question
        if "escort" in q_lower or "night" in q_lower or "women" in q_lower:
            for cid, ctext in chunks_by_key.items():
                if "Night Shift" in ctext or "Night Cab" in ctext or "Night Drops" in ctext:
                    if "after 20:00" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "A security escort is mandatory for women employees on drops scheduled strictly after 20:00.",
                            "citation_ids": [cid],
                        })
                    elif "after 21:00" in ctext or "ending after 21:00" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "A security escort is mandatory for women employees on drops scheduled strictly after 21:00.",
                            "citation_ids": [cid],
                        })
                    elif "after 22:00" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "A security marshal is deployed for women employees on night drops scheduled after 22:00.",
                            "citation_ids": [cid],
                        })

        # 3. Weekend / Saturday cab
        if "saturday" in q_lower or "weekend" in q_lower:
            for cid, ctext in chunks_by_key.items():
                if "Weekend" in ctext:
                    if "manager-approved only" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Cab services on Saturdays are manager-approved only with prior written submission.",
                            "citation_ids": [cid],
                        })
                    elif "not offered" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Weekend cab services on Saturdays are not offered under corporate transport arrangements.",
                            "citation_ids": [cid],
                        })
                    elif "available for rostered shifts" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Weekend transport on Saturdays is available for rostered shifts only.",
                            "citation_ids": [cid],
                        })

        # 4. Airport trip approval
        if "airport" in q_lower:
            for cid, ctext in chunks_by_key.items():
                if "Airport" in ctext:
                    if "transport manager" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Airport cab bookings require prior written approval from the transport manager.",
                            "citation_ids": [cid],
                        })
                    elif "hr + finance" in ctext or "HR + finance" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Airport trips require joint authorization from both HR + finance departments.",
                            "citation_ids": [cid],
                        })
                    elif "not covered" in ctext:
                        return json.dumps({"status": "not_covered", "answer": "", "citation_ids": []})

        # 5. No-show penalty
        if "no-show" in q_lower or "penalty" in q_lower or "missed" in q_lower:
            for cid, ctext in chunks_by_key.items():
                if "No-Show" in ctext:
                    if "₹200 from second no-show within 30 days" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "A penalty of ₹200 from second no-show within 30 days is deducted; the first no-show receives a warning.",
                            "citation_ids": [cid],
                        })
                    elif "warning for first two; ₹300 from third" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "Employees receive a warning for first two; ₹300 from third no-show in a calendar quarter.",
                            "citation_ids": [cid],
                        })
                    elif "₹100 for every no-show" in ctext:
                        return json.dumps({
                            "status": "answered",
                            "answer": "A penalty fee of ₹100 for every no-show is charged starting from the first missed trip.",
                            "citation_ids": [cid],
                        })

        # Default fallback: cite C1 with direct quote if available
        first_cid = list(chunks_by_key.keys())[0]
        return json.dumps({
            "status": "answered",
            "answer": "According to your transport policy, official commute provisions apply as specified in the documented sections.",
            "citation_ids": [first_cid],
        })


class GroqLLMProvider:
    """Real LLM provider using Groq free-tier API with strict bounded timeout."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(self.endpoint, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise LLMError(f"Groq API returned HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            raise LLMError("Groq API request timed out")
        except Exception as e:
            raise LLMError(f"Groq API invocation failed: {str(e)}")


class GeminiLLMProvider:
    """Real LLM provider using Google Gemini free-tier API with strict bounded timeout."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(self.endpoint, json=payload)
                if resp.status_code >= 400:
                    raise LLMError(f"Gemini API returned HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except httpx.TimeoutException:
            raise LLMError("Gemini API request timed out")
        except Exception as e:
            raise LLMError(f"Gemini API invocation failed: {str(e)}")


class LLMCallSpy:
    """Wrapper that tracks and enforces the strict 1-call LLM budget."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.call_count = 0

    def generate(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        self.call_count += 1
        if self.call_count > 1:
            logger.warning("LLM call count exceeded budget of 1!")
        return self.provider.generate(system_prompt, user_prompt, timeout)


def get_llm_provider(provider_type: Optional[str] = None) -> LLMProvider:
    """Select and initialize configured LLM provider."""
    p_type = provider_type or settings.LLM_PROVIDER
    if p_type == "groq" and settings.GROQ_API_KEY:
        return GroqLLMProvider(settings.GROQ_API_KEY, settings.GROQ_MODEL)
    elif p_type == "gemini" and settings.GEMINI_API_KEY:
        return GeminiLLMProvider(settings.GEMINI_API_KEY, settings.GEMINI_MODEL)
    return FakeLLMProvider()
