# Recorded cross-turn diagnostics

`recorded_cross_turn.json` contains the unchanged ES2002a-6 source and F tool
arguments from the B Flash and D Argon runs archived in commit
`f26191487f459d7af7016898422b9bbcfa1a5cee`. The B and D copies of the source are
intentionally separate to preserve the identity of each saved response.

Source: AMI Consortium, AMI Manual Annotations release 1.7, CC BY 4.0.
AMI Meeting Corpus: https://groups.inf.ed.ac.uk/ami/corpus/
License: https://creativecommons.org/licenses/by/4.0/
Native dialogue-act IDs, relative times, original words and headers are retained.
The original archive's upstream_metadata.json and public evidence manifest
provide upstream hashes and license provenance. The analysis script verifies
all 789 archived file hashes and the actual SDK function arguments before
deriving this fixture. No official summary or evaluation labels enter F input.

This is exposed-case diagnostic data, not a held-out benchmark. Tests construct
separately labelled counterfactual quotes in memory. They do not edit the saved
responses and do not measure a model's recall or classification precision.
