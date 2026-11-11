# binance-futures-bot

Bot tự động giao dịch Binance USDT-M Futures (2 danh mục CORE và MAO HIEM), quét mỗi giờ.

- `bn.py`: helper gọi API Binance có ký (env `BINANCE_API_KEY`, `BINANCE_API_SECRET`).
- `tg_trade.py`: gửi thông báo Telegram (env `TELEGRAM_BOT_TOKEN`, chat id trong file riêng, không commit).
- `trade/run_hourly.py`: quét setup (BREAKOUT / RETEST / MOMENTUM), tính rủi ro, đặt lệnh + SL + 2 TP. `--dry-run` để chạy thử.
- `trade/pos_manage.py`: dời SL về hòa vốn sau TP1.
- `trade/pos_watch.py`: watcher 60s, báo TP1 / BE / đóng lệnh và ghi nhật ký.
- `trade/lessons.json`: bài học rút ra từ lệnh đã đóng (chỉ siết luật).
- `trade/trade_journal.jsonl`: nhật ký lệnh đã đóng.

Không có key nào trong repo; tất cả lấy từ biến môi trường.
