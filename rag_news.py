"""新聞 → embedding → pgvector 相似度檢索 → LLM 生成「為什麼選它」報告。
對應 skill_gap_checklist #2（RAG）。embeddings 用 OpenAI text-embedding-3-small（1536 維）。

用法：python rag_news.py --stock 2330 --q "台積電最近基本面與法人動向如何？"
"""
import argparse
import os

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
DB_URL = os.environ["DATABASE_URL"]
client = OpenAI()                      # 讀 OPENAI_API_KEY
EMB_MODEL = "text-embedding-3-small"   # 1536 維，對應 schema 的 vector(1536)
GEN_MODEL = "gpt-4o-mini"              # 之後可換 Claude / 本地模型 / Spring AI


def embed(s: str) -> str:
    """回傳可直接給 pgvector 的字串，如 '[0.01,-0.02,...]'。"""
    vec = client.embeddings.create(model=EMB_MODEL, input=s).data[0].embedding
    return "[" + ",".join(map(str, vec)) + "]"


SAMPLE_NEWS = [
    ("2330", "2026-05-12 09:00", "台積電4月營收創高",
     "台積電公布4月營收，月增與年增雙位數成長，AI 與高效能運算需求強勁帶動先進製程稼動率提升。"),
    ("2330", "2026-05-20 14:00", "外資連續買超台積電",
     "外資近期連續多日買超台積電，法人看好3奈米與2奈米製程放量，調升目標價。"),
    ("2330", "2026-06-03 11:00", "台積電法說會釋出展望",
     "台積電法說會表示全年營收成長動能來自 AI 加速器需求，毛利率維持高檔，資本支出聚焦先進製程。"),
]


def seed_sample_news_if_empty(conn, stock_id):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM news WHERE stock_id=%s", (stock_id,))
        if cur.fetchone()[0] > 0:
            return
        print("news 表為空 → 塞入示範新聞（真實場景請改抓 FinMind taiwan_stock_news 或券商/RSS）")
        for sid, ts, title, content in SAMPLE_NEWS:
            cur.execute(
                "INSERT INTO news (stock_id, published_at, title, content, embedding) "
                "VALUES (%s,%s,%s,%s, %s::vector)",
                (sid, ts, title, content, embed(f"{title}。{content}")),
            )
    conn.commit()


def search(conn, stock_id, query, k=3, as_of=None):
    """語意檢索 Top-K。as_of＝point-in-time：只取「當下已發布」的新聞，避免偷看未來。"""
    sql = ("SELECT published_at, title, content, embedding <=> %s::vector AS dist "
           "FROM news WHERE stock_id=%s")
    params = [embed(query), stock_id]
    if as_of:
        sql += " AND published_at <= %s"
        params.append(as_of)
    sql += " ORDER BY dist LIMIT %s"
    params.append(k)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def answer(conn, stock_id, question, k=3):
    hits = search(conn, stock_id, question, k=k)
    context = "\n\n".join(
        f"[來源{i+1}｜{r[0]:%Y-%m-%d}] {r[1]}\n{r[2]}" for i, r in enumerate(hits)
    )
    prompt = (
        f"你是台股研究助理。只根據下列新聞回答關於 {stock_id} 的問題，"
        f"每個論點標註[來源N]，資訊不足就說不知道、不要編造。\n\n"
        f"新聞：\n{context}\n\n問題：{question}"
    )
    resp = client.chat.completions.create(
        model=GEN_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default="2330")
    ap.add_argument("--q", default="這檔最近基本面與法人動向如何？")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    try:
        seed_sample_news_if_empty(conn, args.stock)
        report, hits = answer(conn, args.stock, args.q)
        print("\n===== 檢索到的新聞（Top-K）=====")
        for i, r in enumerate(hits):
            print(f"[來源{i+1}｜{r[0]:%Y-%m-%d}] {r[1]}  (cosine 距離 {r[3]:.3f})")
        print("\n===== RAG 報告 =====")
        print(report)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
