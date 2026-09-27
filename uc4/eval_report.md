# Live evaluation - the live model against the scripted answers

Provider: `groq`  
Model: `qwen/qwen3.8-27b`  
Temperature: `0.0`  
Prompts: `quality_check_v1`, `extraction_v1`

Nothing was tuned to improve these numbers. Where the model and the script disagree, both are listed so a person can decide which is right - sometimes it will be the script.

> **How the files were sent.** whole document: pdf rasterised to 1 page image(s) in one request. A disagreement on one of those may be about the rendering rather than about the model.

## Document quality

**25/30 documents agree** on the exact flag set.

| case | document | scripted | live | agree |
|---|---|---|---|---|
| WAL-ONB-0001 | ee_fie_registry_extract_mets.pdf | clean | clean | yes |
| WAL-ONB-0001 | passport_mets_k.jpg | clean | clean | yes |
| WAL-ONB-0001 | utility_bill_aug2026_mets.pdf | clean | clean | yes |
| WAL-ONB-0001 | liveness_capture_20260901_mets.jpg | clean | wrong_document_type | **no** |
| WAL-ONB-0001 | source_of_funds_mets.pdf | clean | clean | yes |
| WAL-ONB-0001 | business_activity_mets.pdf | clean | clean | yes |
| WAL-ONB-0002 | cert_incorporation_northbridge.pdf | clean | clean | yes |
| WAL-ONB-0002 | registry_extract_northbridge.pdf | clean | clean | yes |
| WAL-ONB-0002 | director_register_northbridge.pdf | clean | clean | yes |
| WAL-ONB-0002 | signatory_list_northbridge.pdf | clean | clean | yes |
| WAL-ONB-0002 | director_id_halliwell_scan.jpg | blurred_unreadable | blurred_unreadable | yes |
| WAL-ONB-0002 | passport_nankivell_p.jpg | clean | expired | **no** |
| WAL-ONB-0002 | poa_halliwell_jul2026.pdf | clean | name_mismatch | **no** |
| WAL-ONB-0002 | business_activity_northbridge.pdf | clean | clean | yes |
| WAL-ONB-0003 | cert_incorporation_calderwick.pdf | clean | clean | yes |
| WAL-ONB-0003 | registry_extract_calderwick.pdf | clean | clean | yes |
| WAL-ONB-0003 | vat_certificate_calderwick.pdf | clean | clean | yes |
| WAL-ONB-0003 | director_register_calderwick.pdf | clean | clean | yes |
| WAL-ONB-0003 | ubo_declaration_calderwick.pdf | clean | clean | yes |
| WAL-ONB-0003 | passport_kellingray_t.jpg | clean | clean | yes |
| WAL-ONB-0003 | bank_statement_calderwick_aug2026.pdf | clean | missing_pages | **no** |
| WAL-ONB-0004 | registry_extract_vestmark.pdf | clean | clean | yes |
| WAL-ONB-0004 | signatory_list_vestmark.pdf | clean | clean | yes |
| WAL-ONB-0004 | shareholder_register_vestmark.pdf | clean | clean | yes |
| WAL-ONB-0004 | passport_halvorsen_r.jpg | clean | clean | yes |
| WAL-ONB-0004 | poa_halvorsen_jul2026.pdf | clean | name_mismatch, wrong_document_type | **no** |
| WAL-ONB-0006 | cert_incorporation_saarvik.pdf | clean | clean | yes |
| WAL-ONB-0006 | director_register_saarvik.pdf | clean | clean | yes |
| WAL-ONB-0006 | source_of_funds_saarvik.pdf | clean | clean | yes |
| WAL-ONB-0006 | business_activity_saarvik.pdf | clean | clean | yes |

### Quality disagreements, in full

**liveness_capture_20260901_mets.jpg** (WAL-ONB-0001, DOC-0004)
- scripted: `clean`
- live: `['wrong_document_type']` at confidence 0.98
- model notes: The document is a text-based 'LIVENESS CAPTURE' report or certificate, not a selfie (photograph) of the individual. It explicitly states 'No extracted values are recorded for this document' and is marked as a 'SPECIMEN - SYNTHETIC TEST DOCUMENT'.
- call: provider=groq; model=qwen/qwen3.8-27b; prompt=quality_check_v1; temperature=0; attempts=1; latency=23687ms; tokens in/out=2355/111

**passport_nankivell_p.jpg** (WAL-ONB-0002, DOC-0012)
- scripted: `clean`
- live: `['expired']` at confidence 0.95
- model notes: The document is a synthetic identity card marked as a specimen. The name 'Priya Nankivell' matches the checklist. The expiry date is 2029-09-30, which is in the future, but the document is explicitly marked as a 'SPECIMEN' and 'SYNTHETIC TEST DOCUMENT', indicating it is not a valid government-issued ID for verification purposes. However, based strictly on the provided flags, 'expired' is not applicable as the date is in the future. The primary issue is that it is a test document, which falls under 'wrong_document_type' or potentially 'tampering_indicator' if considered a fake, but 'wrong_document_type' is the most accurate fit for a specimen/test card when a real ID is required. Wait, looking at the flags again: 'wrong_document_type' is for when it's legible but not the type asked for. A specimen ID is not a valid ID. Let's re-evaluate. The prompt asks to report what is visible. The document is clearly a test document. Is there a flag for 'test document'? No. 'wrong_document_type' is the closest. Also, the date 2029-09-30 is in the future, so 'expired' is incorrect. I will flag it as wrong_document_type because a specimen is not a valid ID document.
- call: provider=groq; model=qwen/qwen3.8-27b; prompt=quality_check_v1; temperature=0; attempts=1; latency=23796ms; tokens in/out=2357/346

**poa_halliwell_jul2026.pdf** (WAL-ONB-0002, DOC-0013)
- scripted: `clean`
- live: `['name_mismatch']` at confidence 1.00
- model notes: The document is a Utility Account Statement issued for 'Northbridge Craft Supplies Ltd'. The checklist requested a proof of address for 'Denton Halliwell'. The name on the document does not match the name of the person it was supplied for. Additionally, the document is marked as a 'SPECIMEN - SYNTHETIC TEST DOCUMENT' and states 'Invented for testing. This organisation does not exist', indicating it is not a valid piece of evidence.
- call: provider=groq; model=qwen/qwen3.8-27b; prompt=quality_check_v1; temperature=0; attempts=1; latency=208453ms; tokens in/out=2357/160; whole document: pdf rasterised to 1 page image(s) in one request

**bank_statement_calderwick_aug2026.pdf** (WAL-ONB-0003, DOC-0031)
- scripted: `clean`
- live: `['missing_pages']` at confidence 0.90
- model notes: The document is a 'BANK ACCOUNT STATEMENT' as requested, but it is a 'SPECIMEN - SYNTHETIC TEST DOCUMENT' and lacks the transaction history or account details typically found on a usable bank statement. The content is largely empty, suggesting missing pages or an incomplete upload.
- call: provider=groq; model=qwen/qwen3.8-27b; prompt=quality_check_v1; temperature=0; attempts=1; latency=336827ms; tokens in/out=2350/110; whole document: pdf rasterised to 1 page image(s) in one request

**poa_halvorsen_jul2026.pdf** (WAL-ONB-0004, DOC-0045)
- scripted: `clean`
- live: `['name_mismatch', 'wrong_document_type']` at confidence 1.00
- model notes: The document is a 'Utility Account Statement' issued for 'Vestmark Nordic OU', which is an organization, not the individual 'Ruben Halvorsen'. Additionally, the document is explicitly marked as a 'SPECIMEN - SYNTHETIC TEST DOCUMENT' and states 'Invented for testing. This organisation does not exist', making it invalid as proof of address.
- call: provider=groq; model=qwen/qwen3.8-27b; prompt=quality_check_v1; temperature=0; attempts=1; latency=312922ms; tokens in/out=2357/150; whole document: pdf rasterised to 1 page image(s) in one request

## Extracted fields

**60/69 field values agree** across 30 document(s).

| case | document | field | scripted | live |
|---|---|---|---|---|
| WAL-ONB-0002 | registry_extract_northbridge.pdf | registered_address | Suite 3, 88 Fettlers Row, Sheffield S3 8PQ, United Kingdom | Suite 3, 88 Fentlers Row, Sheffield S3 8PQ, United Kingdom |
| WAL-ONB-0002 | director_id_halliwell_scan.jpg | date_of_birth | (absent) | 08-08-98 |
| WAL-ONB-0002 | director_id_halliwell_scan.jpg | document_number | (absent) | 000000000 |
| WAL-ONB-0002 | director_id_halliwell_scan.jpg | expiry_date | (absent) | 08-08-26 |
| WAL-ONB-0002 | director_id_halliwell_scan.jpg | full_name | (absent) | MARTIN TAYLOR |
| WAL-ONB-0003 | poa_ellersby_aug2026.pdf | issuer | (absent) | Calderwick Logistics Ltd |
| WAL-ONB-0004 | poa_vaher_aug2026.pdf | issuer | (absent) | Vestmark Nordic OU (EE-90012345), EE |
| WAL-ONB-0006 | poa_lepik_aug2026.pdf | issuer | (absent) | Saarvik Metall OU (EE-90014782), EE |
| WAL-ONB-0006 | poa_magiste_aug2026.pdf | issuer | (absent) | Saarvik Metall OU (EE-90014782), EE |

## Calls that failed

A failed call is never a pass: in the pipeline each of these would put the document in front of an analyst.

| document | file | error |
|---|---|---|
| DOC-0004 | liveness_capture_20260901_mets.jpg | extraction: extractor returned field(s) ['document_type', 'subject_name', 'document_number', 'issuing_country', 'reference_number', 'date_of_birth', 'nationalit |
| DOC-0013 | poa_halliwell_jul2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': "Request too large for model `qwen/qwen3.8-27b` in organization `org_ |
| DOC-0014 | poa_nankivell_aug2026.pdf | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0014 | poa_nankivell_aug2026.pdf | extraction: the API call failed: APIConnectionError: Connection error. |
| DOC-0015 | source_of_funds_northbridge.pdf | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0021 | signatory_list_calderwick.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0021 | signatory_list_calderwick.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0022 | ubo_declaration_calderwick.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0023 | passport_ellersby_m.jpg | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0024 | passport_delamere_r.jpg | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0024 | passport_delamere_r.jpg | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0025 | passport_kellingray_t.jpg | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0026 | poa_ellersby_aug2026.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0027 | poa_delamere_aug2026.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0027 | poa_delamere_aug2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0028 | poa_kellingray_jul2026.pdf | quality: the API call failed: InternalServerError: Error code: 503 - {'error': {'message': 'qwen/qwen3.8-27b is currently over capacity. Please try again and ba |
| DOC-0028 | poa_kellingray_jul2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0029 | source_of_funds_calderwick.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0030 | business_activity_calderwick.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0030 | business_activity_calderwick.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0031 | bank_statement_calderwick_aug2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0032 | cert_incorporation_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0032 | cert_incorporation_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0033 | registry_extract_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0034 | tax_registration_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0035 | director_register_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0035 | director_register_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0036 | signatory_list_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0037 | ubo_declaration_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0038 | ownership_chart_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0038 | ownership_chart_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0039 | shareholder_register_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0040 | id_card_vaher_l.jpg | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0040 | id_card_vaher_l.jpg | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0041 | id_card_sormus_a.jpg | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0041 | id_card_sormus_a.jpg | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0042 | passport_halvorsen_r.jpg | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0043 | poa_vaher_aug2026.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0044 | poa_sormus_aug2026.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0044 | poa_sormus_aug2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0045 | poa_halvorsen_jul2026.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0046 | source_of_funds_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0047 | business_activity_vestmark.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0047 | business_activity_vestmark.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0063 | cert_incorporation_saarvik.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0064 | registry_extract_saarvik.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0065 | tax_registration_saarvik.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0065 | tax_registration_saarvik.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0066 | director_register_saarvik.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0067 | signatory_list_saarvik.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0068 | ubo_declaration_saarvik.pdf | quality: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01 |
| DOC-0068 | ubo_declaration_saarvik.pdf | extraction: rate limited by groq after 6 attempts: Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` in organization `org |
| DOC-0069 | id_card_kalda_r.jpg | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0069 | id_card_kalda_r.jpg | extraction: the API call failed: APIConnectionError: Connection error. |
| DOC-0070 | id_card_lepik_t.jpg | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0070 | id_card_lepik_t.jpg | extraction: the API call failed: APIConnectionError: Connection error. |
| DOC-0071 | id_card_magiste_o.jpg | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0071 | id_card_magiste_o.jpg | extraction: the API call failed: APIConnectionError: Connection error. |
| DOC-0072 | poa_kalda_sep2026.pdf | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0072 | poa_kalda_sep2026.pdf | extraction: the API call failed: APIConnectionError: Connection error. |
| DOC-0073 | poa_lepik_aug2026.pdf | quality: the API call failed: APIConnectionError: Connection error. |
| DOC-0074 | poa_magiste_aug2026.pdf | quality: the API call failed: APIConnectionError: Connection error. |

## Calls and tokens

**60 call(s)**, 138,620 input and 8,497 output tokens, 22245.9s of model time. 0 needed the retry.

| step | document | attempts | in | out | latency |
|---|---|---|---|---|---|
| quality | ee_fie_registry_extract_mets.pdf | 1 | 2,350 | 128 | 3000ms |
| extraction | ee_fie_registry_extract_mets.pdf | 1 | 2,286 | 292 | 24030ms |
| quality | passport_mets_k.jpg | 1 | 2,354 | 121 | 24281ms |
| extraction | passport_mets_k.jpg | 1 | 2,272 | 207 | 23796ms |
| quality | utility_bill_aug2026_mets.pdf | 1 | 2,355 | 137 | 23530ms |
| extraction | utility_bill_aug2026_mets.pdf | 1 | 2,270 | 164 | 23766ms |
| quality | liveness_capture_20260901_mets.jpg | 1 | 2,355 | 111 | 23687ms |
| quality | source_of_funds_mets.pdf | 1 | 2,352 | 151 | 23625ms |
| extraction | source_of_funds_mets.pdf | 1 | 2,261 | 107 | 23641ms |
| quality | business_activity_mets.pdf | 1 | 2,351 | 118 | 23625ms |
| extraction | business_activity_mets.pdf | 1 | 2,260 | 106 | 23562ms |
| quality | cert_incorporation_northbridge.pdf | 1 | 2,351 | 99 | 24062ms |
| extraction | cert_incorporation_northbridge.pdf | 1 | 2,272 | 200 | 54875ms |
| quality | registry_extract_northbridge.pdf | 1 | 2,350 | 111 | 23625ms |
| extraction | registry_extract_northbridge.pdf | 1 | 2,286 | 296 | 24094ms |
| quality | director_register_northbridge.pdf | 1 | 2,350 | 124 | 23500ms |
| extraction | director_register_northbridge.pdf | 1 | 2,266 | 115 | 23937ms |
| quality | signatory_list_northbridge.pdf | 1 | 2,352 | 117 | 24828ms |
| extraction | signatory_list_northbridge.pdf | 1 | 2,253 | 49 | 22937ms |
| quality | director_id_halliwell_scan.jpg | 1 | 2,356 | 99 | 24203ms |
| extraction | director_id_halliwell_scan.jpg | 1 | 2,272 | 171 | 23233ms |
| quality | passport_nankivell_p.jpg | 1 | 2,357 | 346 | 23796ms |
| extraction | passport_nankivell_p.jpg | 1 | 2,272 | 200 | 23625ms |
| quality | poa_halliwell_jul2026.pdf | 1 | 2,357 | 160 | 208453ms |
| extraction | source_of_funds_northbridge.pdf | 1 | 2,261 | 102 | 6718ms |
| quality | business_activity_northbridge.pdf | 1 | 2,351 | 117 | 2030ms |
| extraction | business_activity_northbridge.pdf | 1 | 2,260 | 103 | 15951687ms |
| quality | cert_incorporation_calderwick.pdf | 1 | 2,351 | 85 | 2703ms |
| extraction | cert_incorporation_calderwick.pdf | 1 | 2,272 | 206 | 23937ms |
| quality | registry_extract_calderwick.pdf | 1 | 2,350 | 117 | 23687ms |
| extraction | registry_extract_calderwick.pdf | 1 | 2,286 | 287 | 23921ms |
| quality | vat_certificate_calderwick.pdf | 1 | 2,351 | 116 | 23750ms |
| extraction | vat_certificate_calderwick.pdf | 1 | 2,258 | 106 | 22657ms |
| quality | director_register_calderwick.pdf | 1 | 2,350 | 115 | 23640ms |
| extraction | director_register_calderwick.pdf | 1 | 2,266 | 114 | 22734ms |
| quality | ubo_declaration_calderwick.pdf | 1 | 2,351 | 137 | 178014ms |
| extraction | passport_ellersby_m.jpg | 1 | 2,272 | 188 | 291203ms |
| quality | passport_kellingray_t.jpg | 1 | 2,357 | 133 | 363563ms |
| extraction | poa_ellersby_aug2026.pdf | 1 | 2,270 | 144 | 337157ms |
| extraction | source_of_funds_calderwick.pdf | 1 | 2,261 | 107 | 205329ms |
| quality | bank_statement_calderwick_aug2026.pdf | 1 | 2,350 | 110 | 336827ms |
| quality | registry_extract_vestmark.pdf | 1 | 2,350 | 111 | 91577ms |
| extraction | tax_registration_vestmark.pdf | 1 | 2,258 | 84 | 272608ms |
| quality | signatory_list_vestmark.pdf | 1 | 2,352 | 115 | 272453ms |
| extraction | ubo_declaration_vestmark.pdf | 1 | 2,285 | 194 | 234608ms |
| quality | shareholder_register_vestmark.pdf | 1 | 2,350 | 118 | 303125ms |
| quality | passport_halvorsen_r.jpg | 1 | 2,356 | 125 | 120797ms |
| extraction | poa_vaher_aug2026.pdf | 1 | 2,270 | 156 | 265311ms |
| quality | poa_halvorsen_jul2026.pdf | 1 | 2,357 | 150 | 312922ms |
| extraction | source_of_funds_vestmark.pdf | 1 | 2,261 | 102 | 271422ms |
| quality | cert_incorporation_saarvik.pdf | 1 | 2,351 | 121 | 336906ms |
| extraction | registry_extract_saarvik.pdf | 1 | 2,286 | 287 | 279000ms |
| quality | director_register_saarvik.pdf | 1 | 2,350 | 145 | 363328ms |
| extraction | signatory_list_saarvik.pdf | 1 | 2,253 | 47 | 429437ms |
| extraction | poa_lepik_aug2026.pdf | 1 | 2,270 | 156 | 4077ms |
| extraction | poa_magiste_aug2026.pdf | 1 | 2,270 | 156 | 7079ms |
| quality | source_of_funds_saarvik.pdf | 1 | 2,352 | 101 | 2327ms |
| extraction | source_of_funds_saarvik.pdf | 1 | 2,261 | 103 | 24578ms |
| quality | business_activity_saarvik.pdf | 1 | 2,351 | 112 | 25327ms |
| extraction | business_activity_saarvik.pdf | 1 | 2,260 | 98 | 23766ms |

At this rate the full set of 61 documents would be roughly 281,820 input tokens across 122 calls.
