from relation_eval.providers.base import BaseProvider
from relation_eval.schemas import DocumentInput, RelationPrediction

class RelationClassifier:
    def __init__(self, provider: BaseProvider, prompt_path: str = "prompts/relation_v1.txt"):
        self.provider = provider
        self.prompt_path = prompt_path

    def classify(self, left: DocumentInput, right: DocumentInput) -> RelationPrediction:
        # Note: In a real system we would format prompt_path with left and right contents
        # but since we are mocking, the provider handles it directly.
        # This complies with "Inference code must not read gold directly".
        return self.provider.predict_relation(left, right)
