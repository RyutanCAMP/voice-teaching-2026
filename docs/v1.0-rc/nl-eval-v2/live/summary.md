# 自然言語の評価（live・claude-sonnet-5-5・4f8b75f-dirty）

- ケース 105・合格 105（100%）・**誤った候補 0**・API の失敗 0・時間 265秒

| 分類 | 件数 | 合格 | 誤った候補 |
|---|---|---|---|
| 同義表現 | 19 | 19 | 0 |
| 単位・語順 | 5 | 5 | 0 |
| 文脈の続き | 11 | 11 | 0 |
| あいまい | 11 | 11 | 0 |
| 訂正 | 3 | 3 | 0 |
| 安全上限 | 11 | 11 | 0 |
| 対象不明 | 8 | 8 | 0 |
| 無関係 | 11 | 11 | 0 |
| 旧候補・旧承認 | 15 | 15 | 0 |
| 聞き違い | 6 | 6 | 0 |
| 否定と指示 | 5 | 5 | 0 |

| ID | 期待 | 結果 | 最後の候補 |
|---|---|---|---|
| NLR-S01 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S02 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S03 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S04 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S05 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S06 | cand:C00004:Y:-3 | OK | C00004 Y -3 |
| NLR-S07 | cand:C00003:X:-2 | OK | C00003 X -2 |
| NLR-S08 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S09 | cand:C00004:Y:-3 | OK | C00004 Y -3 |
| NLR-U01 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-U02 | cand:C00004:Y:-10 | OK | C00004 Y -10 |
| NLR-U03 | cand:C00004:Z:10 | OK | C00004 Z 10 |
| NLR-U04 | cand:C00004:Z:2.5 | OK | C00004 Z 2.5 |
| NLR-U05 | cand:C00004:Y:-3 | OK | C00004 Y -3 |
| NLR-C01 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-C02 | cand:C00003:Y:-3 | OK | C00003 Y -3 |
| NLR-C03 | cand:C00004:Y:5 | OK | C00004 Y 5 |
| NLR-C04 | cand:C00004:Y:3 | OK | C00004 Y 3 |
| NLR-C05 | cand:C00004:Z:2 | OK | C00004 Z 2 |
| NLR-C06 | cand:C00004:Z:4 | OK | C00004 Z 4 |
| NLR-A01 | nocand | OK |  |
| NLR-A02 | nocand | OK |  |
| NLR-A03 | nocand | OK |  |
| NLR-A04 | nocand | OK |  |
| NLR-A05 | nocand | OK |  |
| NLR-A06 | nocand | OK |  |
| NLR-A07 | nocand | OK |  |
| NLR-K01 | nocand | OK |  |
| NLR-K02 | nocand | OK |  |
| NLR-K03 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-L01 | nocand | OK |  |
| NLR-L02 | nocand | OK |  |
| NLR-L03 | nocand | OK |  |
| NLR-L04 | cand:C00004:Z:20 | OK | C00004 Z 20 |
| NLR-L05 | nocand | OK |  |
| NLR-P01 | nocand | OK |  |
| NLR-P02 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-P03 | nocand | OK |  |
| NLR-P04 | noappr | OK | C00005 Z 5 |
| NLR-N01 | nocand | OK |  |
| NLR-N02 | nocand | OK |  |
| NLR-N03 | nocand | OK |  |
| NLR-N04 | nocand | OK |  |
| NLR-N05 | nocand | OK |  |
| NLR-O01 | void | OK |  |
| NLR-O02 | void | OK | C00004 Z 5 |
| NLR-O03 | cand:C00004:Z:3 | OK | C00004 Z 3 |
| NLR-O04 | void | OK | C00004 Z 5 |
| NLR-O05 | nocand | OK |  |
| NLR-O06 | nocand | OK |  |
| NLR-V01 | nocand | OK |  |
| NLR-V02 | nocand | OK |  |
| NLR-V03 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-V04 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-V05 | cand:C00004:Z:-3 | OK | C00004 Z -3 |
| NLR-G01 | nocand | OK |  |
| NLR-G02 | nocand | OK |  |
| NLR-G03 | nocand | OK |  |
| NLR-G04 | nocand | OK |  |
| NLR-S10 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S11 | cand:C00004:Y:-3 | OK | C00004 Y -3 |
| NLR-S12 | cand:C00003:X:1.5 | OK | C00003 X 1.5 |
| NLR-S13 | safe:cand:C00002:Z:3 | OK |  |
| NLR-S14 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S15 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-S16 | safe:cand:C00004:Z:-2 | OK |  |
| NLR-S17 | cand:C00004:Y:3 | OK | C00004 Y 3 |
| NLR-S18 | nocand | OK |  |
| NLR-A08 | nocand | OK |  |
| NLR-A09 | nocand | OK |  |
| NLR-A10 | nocand | OK |  |
| NLR-A11 | nocand | OK |  |
| NLR-L06 | safe:cand:C00004:Z:15 | OK |  |
| NLR-L07 | cand:C00004:Z:20 | OK | C00004 Z 20 |
| NLR-L08 | nocand | OK |  |
| NLR-L09 | nocand | OK |  |
| NLR-L10 | nocand | OK |  |
| NLR-L11 | nocand | OK |  |
| NLR-P05 | nocand | OK |  |
| NLR-P06 | nocand | OK |  |
| NLR-P07 | nocand | OK |  |
| NLR-P08 | nocand | OK |  |
| NLR-C07 | cand:C00004:Y:4 | OK | C00004 Y 4 |
| NLR-C08 | safe:cand:C00004:Z:3 | OK |  |
| NLR-C09 | safe:cand:C00004:Y:-2 | OK |  |
| NLR-C10 | cand:C00002:Z:3 | OK | C00002 Z 3 |
| NLR-C11 | safe:cand:C00004:Z:5 | OK |  |
| NLR-O07 | void | OK | C00004 Z 5 |
| NLR-O08 | void | OK | C00004 Z 5 |
| NLR-O09 | void | OK | C00004 Z 5 |
| NLR-O10 | void | OK |  |
| NLR-O11 | cand:C00004:Z:10 | OK | C00004 Z 10 |
| NLR-O12 | kept | OK | C00004 Z 5 |
| NLR-O13 | kept | OK | C00004 Z 5 |
| NLR-O14 | void | OK | C00004 Z 5 |
| NLR-O15 | void | OK | C00004 Z 5 |
| NLR-N06 | nocand | OK |  |
| NLR-N07 | nocand | OK |  |
| NLR-N08 | nocand | OK |  |
| NLR-N09 | nocand | OK |  |
| NLR-N10 | nocand | OK |  |
| NLR-N11 | nocand | OK |  |
| NLR-S19 | cand:C00004:Z:5 | OK | C00004 Z 5 |
| NLR-V06 | cand:C00004:Z:0.5 | OK | C00004 Z 0.5 |
| NLR-G05 | nocand | OK |  |
