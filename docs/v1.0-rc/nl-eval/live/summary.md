# 自然言語の評価（live・claude-sonnet-5-5・0c11a96-dirty）

- ケース 50・合格 50（100%）・**誤った候補 0**・API の失敗 0・時間 136秒

| 分類 | 件数 | 合格 | 誤った候補 |
|---|---|---|---|
| 同義表現 | 9 | 9 | 0 |
| 単位・語順 | 5 | 5 | 0 |
| 文脈の続き | 6 | 6 | 0 |
| あいまい | 7 | 7 | 0 |
| 訂正 | 3 | 3 | 0 |
| 安全上限 | 5 | 5 | 0 |
| 対象不明 | 4 | 4 | 0 |
| 無関係 | 5 | 5 | 0 |
| 旧候補・旧承認 | 6 | 6 | 0 |

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
| NLR-O05 | cand:C00004:Y:-5 | OK | C00004 Y -5 |
| NLR-O06 | cand:C00004:Z:7 | OK | C00004 Z 7 |
