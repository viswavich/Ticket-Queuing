from flask import Flask, request, jsonify
import requests
import openai
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

API_URL = "https://globalnoticeboard.com/admin/get_client_data_api.php"

priority_order = {
    "Urgent": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3
}

# 🔍 Ticket summarization and priority extraction
def generate_summary_and_priority(title, content, created_time):
    try:
        prompt = f"""You are a smart support ticket analyzer.

Given this support ticket, do the following:
1. Summarize the ticket in 1 short line.
2. Determine the correct priority from (Urgent, High, Medium, Low) based on the title, content, and customer urgency.
3. Determine urgency level from 1 to 5 (1 = most urgent, 5 = least), based on the content and created datetime.

Respond exactly in this format:
Summary: <your one-line summary>
Priority: <Urgent/High/Medium/Low>
Urgency: <1-5>

Title: {title}
Created At: {created_time}
Content: {content}
"""

        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        output = response.choices[0].message.content.strip()
        summary = "N/A"
        priority = "Low"
        urgency_level = 5

        for line in output.split('\n'):
            if line.lower().startswith("summary:"):
                summary = line.split(":", 1)[1].strip()
            elif line.lower().startswith("priority:"):
                p = line.split(":", 1)[1].strip().capitalize()
                if p in priority_order:
                    priority = p
            elif line.lower().startswith("urgency:"):
                try:
                    urgency_level = int(line.split(":", 1)[1].strip())
                except:
                    urgency_level = 5

        return summary, priority, urgency_level

    except Exception as e:
        print(f"🔴 OpenAI Error: {e}")
        return "Could not summarize", "Low", 5

# 🔍 Compute sentiment score per client
def get_sentiment_score(client_id, tickets):
    try:
        combined = "\n".join(
            f"Title: {t['title']}\nContent: {t['content']}" for t in tickets
        )

        prompt = f"""You are a relationship evaluator. Based on this client's complete ticket content, assign a relationship sentiment score between 0 to 10.

Tickets:
{combined}

Respond with only a number between 0 and 10."""

        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        score = float(response.choices[0].message.content.strip())
        return max(0, min(score, 10))
    except Exception as e:
        print(f"⚠️ Sentiment score error for {client_id}: {e}")
        return 0

# 🚀 Prioritize route
@app.route('/prioritize', methods=['POST'])
def prioritize():
    req = request.json
    cnb_ids = req.get("cnb_ids", [])
    new_tickets = req.get("new_tickets", [])

    if not cnb_ids or not isinstance(cnb_ids, list):
        return jsonify({"error": "Send a list of CNB IDs in 'cnb_ids'"}), 400

    # Group new tickets by client_id
    new_tickets_map = {}
    for nt in new_tickets:
        cid = nt.get("client_id")
        if cid:
            new_tickets_map.setdefault(cid, []).append(nt)

    client_blocks = []

    for cnb_id in cnb_ids:
        print(f"\n📡 Fetching data for CNB ID: {cnb_id}")
        try:
            response = requests.post(API_URL, data={"cnb_id": cnb_id})
            raw = response.text.strip()

            if raw.startswith("<pre>") and raw.endswith("</pre>"):
                raw = raw[5:-6].strip()

            try:
                data = json.loads(raw)
            except Exception as e:
                print(f"❌ JSON Parse Error for CNB ID {cnb_id}: {e}")
                continue

            client_name = data.get("cnb_title", "Unknown")
            client_tickets = []

            for _, item in data.items():
                if not isinstance(item, dict):
                    continue

                ticket_number = item.get("cnb_support_ticket_number")
                title = item.get("cnb_support_ticket_title", "")
                content = item.get("cnb_support_ticket_content", "")
                priority = item.get("cnb_support_ticket_priority", "Low")
                created = item.get("cnb_created_datetime", "")

                if not ticket_number or not content:
                    continue

                summary, resolved_priority, urgency_level = generate_summary_and_priority(
                    title, content, created
                )

                final_priority = priority if priority in priority_order else resolved_priority

                client_tickets.append({
                    "ticket_number": ticket_number,
                    "client_id": cnb_id,
                    "client_name": client_name,
                    "title": title,
                    "summary": summary,
                    "priority": final_priority,
                    "urgency": urgency_level,
                    "content": content
                })

            # 👉 Add new tickets from input
            for new_ticket in new_tickets_map.get(cnb_id, []):
                title = new_ticket.get("title", "")
                content = new_ticket.get("content", "")
                created = new_ticket.get("created_at", "")
                ticket_number = new_ticket.get("ticket_number", f"NEW-{len(client_tickets)+1}")

                summary, resolved_priority, urgency_level = generate_summary_and_priority(
                    title, content, created
                )

                input_priority = new_ticket.get("priority", "").capitalize()
                final_priority = input_priority if input_priority in priority_order else resolved_priority

                client_tickets.append({
                    "ticket_number": ticket_number,
                    "client_id": cnb_id,
                    "client_name": client_name,
                    "title": title,
                    "summary": summary,
                    "priority": final_priority,
                    "urgency": urgency_level,
                    "content": content
                })

            # Score per client only if multiple
            sentiment_score = get_sentiment_score(cnb_id, client_tickets) if len(cnb_ids) > 1 else 0

            # Sort by priority then urgency
            client_tickets.sort(key=lambda x: (
                priority_order.get(x["priority"], 4),
                x["urgency"]
            ))

            client_blocks.append({
                "sentiment_score": sentiment_score,
                "client_id": cnb_id,
                "tickets": client_tickets
            })

        except Exception as e:
            print(f"❌ Error on CNB ID {cnb_id}: {e}")
            continue

    # Sort clients by sentiment score if more than 1 client
    if len(cnb_ids) > 1:
        client_blocks.sort(key=lambda x: -x["sentiment_score"])

    # Final output
    final_output = []
    for block in client_blocks:
        for t in block["tickets"]:
            if len(cnb_ids) > 1:
                t["sentiment_score"] = block["sentiment_score"]
            del t["content"]
            del t["urgency"]
            final_output.append(t)

    return jsonify(final_output)

# 🔁 Start app
if __name__ == "__main__":
    app.run(debug=True)
