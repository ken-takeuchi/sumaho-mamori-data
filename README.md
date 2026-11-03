# sumaho-mamori-data

スマホまもり（自作の着信判定アプリ）が使う電話番号データです。

- `numbers.tsv`：電話番号の頭の桁ごとに、番号を割り当てられた会社・地域・使用状況をまとめたもの
- 毎月1回、GitHub Actions が総務省のサイトから最新のファイルを取得して作り直します
- アプリは月に1回、Wi-Fi接続時にこのファイルを取りに来ます

## 出典

総務省「電気通信番号指定状況」「市外局番の一覧」（CC BY）を加工して作成しています。

- https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html

携帯電話番号の会社は「番号を割り当てられた会社」です。乗り換え（MNP）で実際の契約会社と違う場合があります。
