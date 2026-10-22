# AMI official-link retrieval cohort v1

AMI Meeting Corpus manual annotations, University of Edinburgh / AMI
Consortium, distributed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The original license bytes are in `frozen/LICENCE.txt`.

Source: <https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip>

Archive SHA-256: `b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d`.
The filename says 1.6.2; its internal README says manual release 1.7,
2014-06-16. The hash fixes the actual source version.

This new directory copies the prior Phase 0 query, official qrel, reference
prediction, code, and seal bytes without changing their originals. The freeze
manifest fixes their hashes. The corpus projection was computed only after
checking every original DA JSONL against the reference run's corpus hash. It
binds native DA ID, text, and native word IDs independently of canonical layout.
Custodian preparation re-resolves the official summary and summlink XML from
the fixed archive before producing new evaluation inputs.

Official summary sentences are the declared questions, not retrieved documents.
All qrels and original summary/link XML are evaluator-only. Links are incomplete
human relevance judgements; unlinked DA records are not negative labels.

The ES2002 series is development and ES2005 is a previously exposed diagnostic
series. Both were AMI SA train. There are 36/20 questions and 33/18 scoreable
questions respectively. Five questions have no official links and remain N/E.
No held-out, pretraining-free, or Product end-to-end accuracy claim is made.

The reference outputs are an archived `rank-bm25==0.2.2` experiment, not a new
LinkLoom ranker. The `.py.txt` files preserve the original runner/scorer for
audit; the new prediction command uses existing production retrieval classes.
