# Paid canary approval record

> This file is the record of a **real, human-performed** paid
> generation. It was created by the human who ran the generation,
> not synthesized by the model.

- **submit_id:** `bff07abc-a2f6-473b-b53d-38c7bd7b492c`
- **timestamp:** 2026-09-13T03:41:39Z
- **approver:** wandl

## Observed behavior

One text2image request using model 5.0Pro, resolution_type 1.5k, count 1, ratio 1:1 reached gen_status=success. The first download returned ret=1015; retrying query_result for the same submit_id succeeded. The verified PNG is 1536x1536, 199913 bytes, SHA-256 c4b3d50cdd9e342ac0ac9c822d78453b76a8b58cac5863a1e7eafb993aa56e38. commerce_info.credit_count=0 and benefit_type=image_basic_v50_pro_15k.

## How this was produced

1. Installed and authorized the `dreamina` CLI in an
   authorized shell.
2. Ran one low-cost image or video generation interactively.
3. Captured the server-issued submit ID above.
4. Ran `python3 scripts/unlock_runtime_gates.py record-canary
   --submit-id <id> --approver <name> --observed "<summary>"`.
