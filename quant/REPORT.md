# Báo cáo quant: backtest luật live và bài học từ lệnh thật

Ngày chạy: 11/11/2026 (giờ Sài Gòn, UTC+7). Dữ liệu: 11/11/2024 tới 11/11/2026, 19 coin, nến 1h và 15m, lịch sử funding thật.
Toàn bộ code nằm trong `quant/`. Không đụng file live và không đặt hay hủy lệnh nào. Chỉ dùng API public và API chỉ đọc (income, userTrades, allOrders).

## 1. Kết luận ngắn

**Luật live hiện tại không có edge, mà còn lỗ rõ rệt.**
- Tính trên từng coin độc lập: 11.837 lệnh trong 2 năm, trung bình **−0.19R/lệnh** (khoảng tin cậy 95%: −0.21 đến −0.17), PF 0.70. Có 0/25 tháng dương.
- Mô phỏng đủ luật danh mục (1 lệnh mỗi book, 2 lệnh tổng, daily stop, giờ ra tin Mỹ, nhóm tương quan) với khối lượng như bây giờ (CORE 2 USDT, MẠO HIỂM 6.75 USDT rủi ro mỗi lệnh): **−1.515 USDT trong 2 năm** (1.975 lệnh, −0.24R/lệnh).
- Mô phỏng lãi kép bắt đầu từ 45 USDT: **ví về dưới 5 USDT vào ngày 15/01/2025**, tức sau 2 tháng và 173 lệnh.

**Thủ phạm chính là setup RETEST.** RETEST chiếm 83% số lệnh, trung bình −0.23R. Nó âm ở mọi nhóm đã thử: mọi mức điểm, mọi tuổi break (0–7h), cả long lẫn short, cả CORE lẫn MẠO HIỂM, từng coin, từng quý. Ngay cả khi tính trước phí, RETEST vẫn âm (−0.08R). Khi cộng phí và trượt giá, với SL hẹp (trung bình 0.81%, tức 0.67×ATR) thì chi phí ăn thêm khoảng 0.15R mỗi lệnh.

**Phần còn sống được là BREAKOUT có SL ≥ 1×ATR:** +0.05R/lệnh (n=2.128). Năm đầu +0.051, năm sau +0.054. Mô phỏng danh mục khối lượng cố định cho +140 USDT/2 năm, maxDD 21R. Nhưng khoảng tin cậy vẫn chứa 0 (−0.00 đến +0.11). **Đây chưa phải bằng chứng có edge, chỉ là phiên bản ít tệ nhất.**

Walk-forward cho kết quả tương tự. Tối ưu trên 6 tháng rồi kiểm tra trên 2 tháng kế tiếp, lặp 9 lần: in-sample đạt +0.071R nhưng out-of-sample chỉ còn **−0.001R** (n=1.597). Luật live trên cùng các cửa sổ kiểm tra cho −0.21R. Tối ưu tham số giúp tránh lỗ nhưng không tạo ra lãi. Mức rơi IS→OOS khoảng 0.07R là dấu hiệu overfit.

## 2. Cách làm (tóm tắt)

- `fetch_data.py`: tải klines 1h, 15m và funding, có tôn trọng weight header và tự lùi khi gặp lỗi 418/429.
- `fetch_account.py`: tải income, userTrades, allOrders từ ngày 02/11 (chỉ đọc).
- `strategy.py`: chép lại nguyên `scan_sym()` của `run_hourly.py`. Không import trực tiếp được vì script live tự chạy, khóa lock và đặt lệnh ngay khi import. Các điểm khác live:
  - Dùng giá last thay cho mark price. SL/TP live kích hoạt theo MARK_PRICE.
  - Giờ quét: giá "mark" lấy bằng close của nến 15m đầu giờ (khoảng :15). Volume nến đang chạy lấy bằng 1/3 volume nến 15m đó, để giả lập lúc quét khoảng :05 như live.
  - Xu hướng 4h gộp từ nến 1h (mốc 4h UTC).
  - Không mô phỏng tickSize/stepSize. minNotional 5 USDT.
  - lessons.json giữ cố định ở trạng thái hiện tại: RETEST −0.5, tất cả −0.25, TP1 ≤ 1.5R, phí 0.055%. Không mô phỏng việc tự cập nhật theo chuỗi thắng thua.
  - Kiểm tra độ khớp với 55 dòng quét thật trong `hourly_log.jsonl`: 71% cùng setup và cùng chiều. Trong số khớp, 72% có SL lệch không quá 0.1% (trung vị 0%). Phần lệch chủ yếu do bản port quét lúc :15, còn live quét lúc :55–:05.
- `engine.py`:
  - Vào lệnh market ở nến 15m kế tiếp, trượt giá 0.05% bất lợi. Lệnh limit khớp khi giá chạm (+0.02%), hủy sau 2h.
  - SL/TP mô phỏng trên nến 15m theo hướng bi quan: nếu cùng một nến chạm cả SL và TP thì tính SL trước. Trường hợp này chỉ chiếm 0.15% số lệnh nên không làm méo kết quả.
  - TP1 chốt 50%, sau đó dời SL về entry ±0.10% từ nến kế tiếp (đúng như pos_manage). Phần còn lại chạy tới TP2.
  - Phí 0.055% mỗi chiều. Funding lấy từ lịch sử thật. Exit SL trượt 0.02%.
  - Danh mục: daily stop −7.5 USDT theo ngày Sài Gòn, luật "room" của MẠO HIỂM, giờ ra tin Mỹ, nhóm tương quan tĩnh, sizing và tự hạ đòn bẩy giống live.
- `walkforward.py`, `run_portfolio_tests.py`, `correlation.py`, `live_history.py`, `extra_charts.py`: tạo các bảng bên dưới. Kết quả thô nằm trong `results/*.json`.

Ghi chú về chỉ số: R là lời/lỗ chia cho rủi ro dự kiến, đã gồm 2 lần phí như cách live tính khối lượng, và đã trừ phí, trượt giá, funding. "Sharpe" tính từ R theo ngày, quy ra năm.

## 3. Kết quả luật live (in-sample, toàn bộ 2 năm)

### 3a. Từng coin độc lập (không giới hạn slot, để mẫu đủ lớn)

| Nhóm | Lệnh | Thắng | TB R | PF | Sharpe |
|---|---|---|---|---|---|
| Tất cả | 11.837 | 41% | −0.187 | 0.70 | −7.1 |
| CORE | 3.224 | 42% | −0.263 | 0.59 | −7.3 |
| CORE / RETEST | 3.070 | 42% | −0.277 | 0.57 | −7.5 |
| CORE / BREAKOUT | 154 | 44% | +0.008 | 1.01 | 0.0 |
| MẠO HIỂM | 8.613 | 41% | −0.159 | 0.74 | −5.6 |
| MẠO HIỂM / RETEST | 6.806 | 40% | −0.209 | 0.67 | −7.4 |
| MẠO HIỂM / BREAKOUT | 1.801 | 44% | +0.032 | 1.06 | 0.5 |
| MẠO HIỂM / MOMENTUM | 6 | 33% | −0.451 | 0.34 | – |

Chi phí theo setup:
- RETEST: trước phí −0.083R, phí 0.146R, SL trung bình 0.81% (0.67×ATR).
- BREAKOUT: trước phí +0.077R, phí 0.044R, SL 3.2% (1.86×ATR).

Độ nhạy:
- Trượt giá 0: −0.125R.
- Trượt giá 0.1%: −0.258R.
- Phí thật 0.05%: −0.178R.

Bỏ toàn bộ lessons (luật gốc ngày 10/11) cũng không khá hơn: −0.169R.

### 3b. Danh mục đầy đủ, khối lượng cố định (CORE 2 USDT, MẠO HIỂM 6.75 USDT mỗi R)

| Nhóm | Lệnh | Thắng | TB R | PF | Ghi chú |
|---|---|---|---|---|---|
| Tất cả | 1.975 | 41% | −0.236 | 0.63 | −1.515 USDT, maxDD 467R, 3/25 tháng dương |
| CORE | 1.174 | 41% | −0.291 | 0.56 | |
| MẠO HIỂM | 801 | 41% | −0.154 | 0.75 | |

Mô phỏng lãi kép từ 45 USDT: về 4.7 USDT vào ngày 15/01/2025. Biểu đồ: `results/equity_portfolio_usdt.png`, `results/equity_portfolio_fixed_usdt.png`.

## 4. Walk-forward / out-of-sample

Bộ tham số tối ưu có 972 tổ hợp:
- Điểm tối thiểu: +0, +0.5 hoặc +1.0.
- Hệ số ATR của SL: 0.3, 0.4 hoặc 0.6.
- TP1: 1.0, 1.5 hoặc 2.0R.
- Khoảng retest: 0.4% hoặc 0.8%.
- Tuổi break: 3, 5 hoặc 8h.
- Chặn vào lại cùng mức: có hoặc không.
- SL tối thiểu: 0, 0.75 hoặc 1.0×ATR.

Tối ưu trên 6 tháng (mục tiêu là tổng R, cần ít nhất 40 lệnh), kiểm tra trên 2 tháng kế tiếp, lặp 9 lần.

| | Lệnh | TB R | Khoảng tin cậy 95% |
|---|---|---|---|
| In-sample (các cửa sổ train) | 5.104 | +0.071 | +0.036..+0.106 |
| **Out-of-sample (ghép 9 cửa sổ test)** | 1.597 | **−0.001** | −0.062..+0.061 |
| OOS: CORE / BREAKOUT | 62 | +0.383 | +0.06..+0.71 (mẫu nhỏ, có thể may mắn) |
| OOS: CORE / RETEST | 27 | −0.175 | |
| OOS: MẠO HIỂM / BREAKOUT | 1.334 | +0.019 | −0.05..+0.09 |
| OOS: MẠO HIỂM / RETEST | 174 | −0.261 | −0.43..−0.09 |
| Luật live trên cùng cửa sổ test | 8.961 | −0.212 | −0.235..−0.188 |

- Có 5/9 cửa sổ test dương.
- Tham số được chọn khá ổn định giữa các cửa sổ:
  - SL tối thiểu 1.0×ATR: chọn 9/9 lần.
  - Tuổi break 3h: 9/9.
  - TP1 2.0R: 8/9.
  - Điểm +0.5 hoặc +1.0: 9/9.
- Cả SL tối thiểu lẫn tuổi break đều được chọn ở biên của lưới. Nghĩa là "lọc càng nhiều càng đỡ lỗ", chứ không phải đã tìm ra điểm tối ưu có lãi.
- Danh mục OOS khối lượng cố định cho +0.004R (381 lệnh, −25 USDT). Bản lãi kép cho 45 → 96.5 USDT, nhưng chỉ có 93 lệnh và khoảng tin cậy chứa 0, nên không đáng tin.
- **Cờ overfit:** in-sample +0.07R nhưng OOS ≈ 0. Top 10 tổ hợp tốt nhất toàn kỳ (in-sample) chỉ đạt +0.048R.

Biểu đồ: `results/equity_walkforward_oos_usdt.png`.

## 5. Các giả thuyết rút từ lệnh thua thật (từng coin độc lập, 2 năm)

| Luật thử | Lệnh | TB R | Năm 1 | Năm 2 | Danh mục cố định (USDT/2 năm) |
|---|---|---|---|---|---|
| Luật live | 11.837 | −0.187 | −0.162 | −0.213 | −1.515 |
| (a) Không retest lại cùng mức | 10.304 | −0.183 | −0.158 | −0.208 | −1.512 |
| (b) Break không quá 0h / 1h / 2h / 3h | 6.453 / 8.489 / 9.617 / 10.336 | −0.153 / −0.170 / −0.175 / −0.179 | | | −792 / −1.088 / −1.359 / −1.382 |
| (c) SL ≥ 0.75×ATR | 5.445 | −0.118 | −0.078 | −0.158 | −795 |
| (c) SL ≥ 1.0×ATR | 3.413 | −0.054 | −0.011 | −0.100 | −247 |
| (c) SL ≥ 1.25×ATR | 2.455 | +0.010 | +0.031 | −0.017 | +35 |
| Bỏ RETEST (chỉ BREAKOUT) | 2.196 | +0.031 | +0.032 | +0.030 | +73 |
| **Chỉ BREAKOUT + SL ≥ 1×ATR** | 2.128 | **+0.052** | +0.051 | +0.054 | **+140** (maxDD 21R, 14/25 tháng dương) |
| Chỉ BREAKOUT + SL 1–2×ATR (chọn sau khi xem dữ liệu) | 1.498 | +0.098 | +0.132 | +0.051 | +236 |

Kết luận cho từng giả thuyết:
- **(a)** và **(b)** gần như không đổi được kỳ vọng lệnh. Chúng chỉ bớt số lệnh lỗ chứ không biến RETEST thành có lãi.
- **(c)** là giả thuyết đúng nhất: SL quá sát so với nhiễu chính là vấn đề. Nhưng RETEST có SL ≥ 1.25×ATR vẫn −0.15R.

Biểu đồ: `results/equity_variants_R.png`, `results/equity_portfolio_fixed_compare_usdt.png`.

## 6. Tương quan

- Tương quan 30 ngày trung bình giữa 19 coin là **0.72**. Cặp thấp nhất cũng 0.60 (BNB–WLD). Có 31% số cặp và ngày vượt 0.8.
- Các cặp cao nhất: ETH–LINK 0.84, ARB–OP 0.84, BTC–ETH 0.84, LINK–AVAX 0.82, DOGE–PEPE 0.82.
- Trong danh mục mô phỏng có 951 lần CORE và MẠO HIỂM mở lệnh chồng thời gian:
  - 748 lần cùng chiều (tương quan trung bình 0.68).
  - 200 lần cả hai cùng thua khi cùng chiều, so với 48 lần khi ngược chiều.
- Luật "chặn lệnh cùng chiều nếu tương quan > 0.8" **không cải thiện kết quả**. Luật live đi từ −0.236 xuống −0.243R. BREAKOUT + SL 1×ATR đi từ +0.041 xuống +0.027R. Nhóm tĩnh hiện tại cũng không đổi gì.
- Thực tế là 2 lệnh cùng chiều gần như luôn là một cược gấp đôi. Cách kiểm soát đúng là giảm rủi ro mỗi lệnh, chứ không phải một ngưỡng tương quan.

Biểu đồ: `results/correlation_heatmap.png`, `results/correlation_over_time.png`.

## 7. Học từ lịch sử thật (từ 02/11)

- **Tài khoản:** realized −2.36 USDT, phí −1.96, funding −0.02. Tổng giao dịch **−4.34 USDT**. Phí bằng khoảng 83% số lỗ realized.
- **Phí thật:** taker 0.050%, maker 0.020%. Mức 0.055% trong lessons là thận trọng, giữ nguyên.
- **Trượt giá vào lệnh market:** lệnh khớp 2–3 phút sau lúc quét. Lệch so với giá dự tính từ −0.18% (thuận lợi) tới +0.07% (bất lợi), trung bình thuận 0.07%. Nhưng độ dao động ±0.13% tương đương 0.2–0.3R khi SL chỉ 0.4–0.6%.
- **Trượt giá SL:** gần 0 (−0.02% tới +0.04%). Có một lệnh limit (BNB) khớp sau 157 phút, tức là live để lệnh chờ tới khoảng 3h chứ không phải 2h.
- **Bảng 7 lệnh:**

| Lệnh | Setup | SL/ATR | MFE | Kết quả | (a) | (b ≤ 3h) | (c ≥ 1 ATR) | Bỏ RETEST |
|---|---|---|---|---|---|---|---|---|
| ARB long 06/11 | BREAKOUT | 0.41 | 1.69R | −8.46 | | | chặn | |
| XRP short 07/11 | pullback cũ | 0.70 | 0.52R | −1.72 | | | chặn | |
| SOL short 10/11 | RETEST | ~0.49 | 3.3% | +3.96 | | | chặn | chặn |
| BTC short 10/11 | RETEST | 0.70 | 1.23R | +0.76 | | | chặn | chặn |
| BTC short 11/11 | RETEST (vào lại mức 73368) | 0.69 | 0.63R | −1.49 | chặn | | chặn | chặn |
| BNB short 11/11 | RETEST (break 5h) | 0.47 | 0.54R | −1.63 | | chặn | chặn | chặn |
| ARB short 10/11 (đang mở) | BREAKOUT | 2.57 | 2.10R | +4.29 đã chốt, phần còn lại SL ở hòa vốn | | | | |

- **Nhìn từ bảng:**
  - Cả 4 lệnh thua đều có SL < 0.75×ATR. Lệnh thắng lớn nhất (ARB short) có SL 2.57×ATR.
  - Nếu áp luật (c) SL ≥ 1×ATR: tránh được −13.30 USDT lỗ nhưng mất +4.72 lãi, ròng +8.58 USDT.
  - Bỏ RETEST trên 7 lệnh này thì ròng −1.60, vì SOL thắng đẹp.
  - 7 lệnh là quá ít để kết luận. Con số đáng tin là 2 năm backtest, và nó nói RETEST −0.23R.
- **Lệnh thật so với mô phỏng:** chạy engine trên đúng kế hoạch của từng lệnh thật cho cùng kết quả ở cả 5 lệnh có SL rõ (SL / SL / TP1→BE / SL / SL). Riêng ARB long 06/11, MFE trong mô phỏng thấp hơn journal, do mô phỏng tính từ nến 15m sau lúc vào. Điều này cho thấy engine phản ánh đúng cách lệnh thật chạy.

## 8. Đề xuất (chi tiết trong `proposed_changes.json`)

### A. Chỉ làm luật chặt hơn (được tự áp dụng theo chính sách của bạn; mình CHƯA áp dụng)

| | Thay đổi | Bằng chứng | Tác động dự kiến |
|---|---|---|---|
| A1 | Chặn RETEST ở cả 2 book (`score_penalty.RETEST = 99`, không cần sửa code) | −0.23R trên 9.876 lệnh, âm 25/25 tháng, OOS vẫn âm | Danh mục 2 năm từ −1.515 lên +73 USDT. Còn khoảng 5 lệnh/tuần |
| A2 | Chặn MOMENTUM (`score_penalty.MOMENTUM = 99`) | n nhỏ, toàn âm | Nhỏ, giảm rủi ro |
| A3 | Bỏ lệnh có SL < 1×ATR(1h,14) (cần thêm 1 bộ lọc trong script) | Cả 4 lệnh thua thật đều SL hẹp. BREAKOUT +0.031 lên +0.052R, dương cả 2 năm | Từ +73 lên +140 USDT/2 năm, maxDD 29R xuống 21R |
| A4 | Giảm rủi ro xuống ≤1% ví mỗi lệnh (khoảng 0.45 USDT) cho cả 2 book, thay cho 2 USDT / 15% ví | Ngay cả phương án tốt nhất cũng có maxDD khoảng 21R. Với mức hiện tại thì 21R lớn hơn cả ví. 13/18 biến thể chạy ví về dưới 5 USDT | Không đổi R/lệnh, giảm 4–15 lần mức lỗ tối đa |
| A5 | Không vào lại cùng một mức lần 2 | Vô hại nhưng tác động gần 0 ở quy mô lớn | Nhỏ |
| A6 (tùy chọn, độ tin thấp) | Trần SL ≤ 2×ATR cho BREAKOUT | Ngưỡng được chọn sau khi xem dữ liệu, năm 2 yếu | Có thể từ +140 lên +236 USDT, nhưng dễ là may mắn |
| A7 (tùy chọn) | Hạ daily stop về khoảng −3× rủi ro mỗi lệnh | Tỉ lệ thuận với A4 | – |

### B. Nới lỏng hoặc đổi hành vi (cần bạn duyệt)

| | Thay đổi | Ý kiến |
|---|---|---|
| B1 | Tạm dừng giao dịch thật hoặc chạy `--dry-run` cho tới khi bộ A1+A3 có ít nhất 50 lệnh dương | Khuyên cân nhắc, vì chưa chứng minh được edge |
| B2 | Nâng trần TP1 từ 1.5R lên 2.0R | Không đáng: walk-forward chọn 2.0 nhưng tác động khoảng 0 |
| B3 | Đổi tham số quét theo walk-forward | Không nên: OOS ≈ 0, chủ yếu tác động RETEST |
| B4 | Luật tương quan động > 0.8 | Không nên: đã thử, không cải thiện |
| B5 | Nghiên cứu vào BREAKOUT bằng limit (maker 0.02%) | Đáng thử tiếp, vì phí là đòn bẩy lớn nhất còn lại |

## 9. Thật lòng

- Bot hiện tại thua là do cấu trúc luật, không phải do xui. Cơ chế "học" hiện tại (trừ điểm sau chuỗi thua) không sửa được một setup có kỳ vọng âm.
- Phiên bản tốt nhất tìm được (chỉ BREAKOUT, SL ≥ 1×ATR) chỉ quanh hòa vốn đến dương nhẹ, chưa đủ để gọi là edge. Nếu tiếp tục chạy thật, nên chạy với rủi ro rất nhỏ (A4) và coi đó là giai đoạn thu dữ liệu.
