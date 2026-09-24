# Live evaluation - the live model against the scripted answers

Provider: `groq`  
Model: `qwen/qwen3.8-27b`  
Prompts: `quality_check_v1`, `extraction_v1`

Nothing was tuned to improve these numbers. Where the model and the script disagree, both are listed so a person can decide which is right - sometimes it will be the script.

> **How the files were sent.** pdf rasterised to 1 page image(s). A disagreement on one of those may be about the rendering rather than about the model.

## Document quality

**2/2 documents agree** on the exact flag set.

| case | document | scripted | live | agree |
|---|---|---|---|---|
| WAL-ONB-0001 | ee_fie_registry_extract_mets.pdf | clean | clean | yes |
| WAL-ONB-0001 | passport_mets_k.jpg | clean | clean | yes |

## Extracted fields

**7/8 field values agree** across 2 document(s).

| case | document | field | scripted | live |
|---|---|---|---|---|
| WAL-ONB-0001 | ee_fie_registry_extract_mets.pdf | registered_address | Kastani 12-4, 51006 Tartu, Estonia | Kastari 12-4, 51006 Tartu, Estonia |

## Calls and tokens

**4 call(s)**, 9,262 input and 772 output tokens, 52.5s of model time. 0 needed the retry.

| step | document | attempts | in | out | latency |
|---|---|---|---|---|---|
| quality | ee_fie_registry_extract_mets.pdf | 1 | 2,350 | 112 | 2702ms |
| extraction | ee_fie_registry_extract_mets.pdf | 1 | 2,286 | 288 | 1407ms |
| quality | passport_mets_k.jpg | 1 | 2,354 | 165 | 24796ms |
| extraction | passport_mets_k.jpg | 1 | 2,272 | 207 | 23625ms |

At this rate the full set of 61 documents would be roughly 282,430 input tokens across 122 calls.
