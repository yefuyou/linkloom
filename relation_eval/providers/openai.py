import json
import time
from pathlib import Path
from openai import OpenAI
from relation_eval.providers.base import BaseProvider
from relation_eval.schemas import DocumentInput, TopicPrediction, RelationPrediction

class OpenAIProvider(BaseProvider):
    def __init__(self, api_key: str, base_url: str | None = None, model: str = "gpt-4o-mini"):
        if base_url:
            base_url = base_url.strip()
            # If base_url is specified but doesn't end with /v1, append it for OpenAI client standard compatibility
            if not base_url.endswith("/v1") and not base_url.endswith("/v1/"):
                base_url = base_url.rstrip("/") + "/v1"
        
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        self.model = model
        self.request_count = 0
        self.input_tokens = 0
        self.output_tokens = 0

        # Load prompts path bases
        self.prompt_dir = Path("prompts")

    def _clean_json(self, content: str) -> str:
        s = content.strip()
        if s.startswith("```json"):
            s = s[7:]
        elif s.startswith("```"):
            s = s[3:]
        if s.endswith("```"):
            s = s[:-3]
        return s.strip()

    def predict_topic(self, doc: DocumentInput) -> TopicPrediction:
        prompt_path = self.prompt_dir / "topic_v1.txt"
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        prompt_text = template.replace("{{content}}", doc.content)

        # Call OpenAI Chat Completions
        time.sleep(1.5)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt_text}],
            response_format={"type": "json_object"}
        )

        if response.usage:
            self.request_count += 1
            self.input_tokens += response.usage.prompt_tokens
            self.output_tokens += response.usage.completion_tokens

        raw_content = response.choices[0].message.content
        cleaned = self._clean_json(raw_content)
        data = json.loads(cleaned)

        # Inject note_path
        data["note_path"] = doc.note_path
        return TopicPrediction.model_validate(data)

    def predict_relation(self, left: DocumentInput, right: DocumentInput) -> RelationPrediction:
        prompt_path = self.prompt_dir / "relation_v1.txt"
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        prompt_text = template.replace("{{left_note_path}}", left.note_path)
        prompt_text = prompt_text.replace("{{left_content}}", left.content)
        prompt_text = prompt_text.replace("{{right_note_path}}", right.note_path)
        prompt_text = prompt_text.replace("{{right_content}}", right.content)

        time.sleep(1.5)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt_text}],
            response_format={"type": "json_object"}
        )

        if response.usage:
            self.request_count += 1
            self.input_tokens += response.usage.prompt_tokens
            self.output_tokens += response.usage.completion_tokens

        raw_content = response.choices[0].message.content
        cleaned = self._clean_json(raw_content)
        data = json.loads(cleaned)

        # Inject note paths
        data["left_note_path"] = left.note_path
        data["right_note_path"] = right.note_path
        return RelationPrediction.model_validate(data)
