import abc
from relation_eval.schemas import DocumentInput, TopicPrediction, RelationPrediction

class BaseProvider(abc.ABC):
    @abc.abstractmethod
    def predict_topic(self, doc: DocumentInput) -> TopicPrediction:
        pass

    @abc.abstractmethod
    def predict_relation(self, left: DocumentInput, right: DocumentInput) -> RelationPrediction:
        pass
