from flask import Flask, render_template_string, request, jsonify, send_file
import threading
import queue
import re
import dns.resolver
import smtplib
import socket
import csv
import io
import time
import random

app = Flask(__name__, static_url_path='/static', static_folder='static')

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")

results = []
lock = threading.Lock()

resolver = dns.resolver.Resolver()
resolver.timeout = 3
resolver.lifetime = 3
resolver.nameservers = ['8.8.8.8', '1.1.1.1']

FAKE_SENDERS = ["ed@bimservices.net"]
MAX_RETRIES = 3
RETRY_DELAY = 5

def catch_all_check(mx_record, domain):
    test_email = f"noexist-{random.randint(100000,999999)}@{domain}"
    sender = random.choice(FAKE_SENDERS)
    try:
        server = smtplib.SMTP(mx_record, 25, timeout=7)
        server.helo("yourdomain.com")
        server.mail(sender)
        code, _ = server.rcpt(test_email)
        server.quit()
        return code == 250
    except:
        return False

def is_valid_email(email, retry_count=MAX_RETRIES):
    if not EMAIL_REGEX.match(email):
        return email, "Invalid format", "danger", "❌"

    domain = email.split('@')[-1]
    try:
        mx_records = resolver.resolve(domain, 'MX')
        mx_record = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip('.')
    except dns.resolver.NXDOMAIN:
        return email, "Domain does not exist", "warning", "⚠️"
    except Exception:
        return email, "DNS lookup failed or domain invalid", "warning", "⚠️"

    sender = random.choice(FAKE_SENDERS)
    time.sleep(random.uniform(1.0, 2.5))

    try:
        server = smtplib.SMTP(mx_record, 25, timeout=7)
        server.set_debuglevel(0)
        server.helo("yourdomain.com")
        try:
            server.starttls()
            server.ehlo()
        except:
            pass

        server.mail(sender)
        code, message = server.rcpt(email)
        server.quit()

        message_str = message.decode().lower() if isinstance(message, bytes) else str(message).lower()

        if code == 250:
            if any(term in message_str for term in ["user unknown", "not found", "no such user", "recipient rejected", "unrouteable"]):
                return email, "Mailbox not found", "danger", "❌"
            return email, "Mailbox exists", "success", "✅"

        elif code in (450, 451, 452) or any(word in message_str for word in ["try again", "temporarily", "greylist", "over quota"]):
            if retry_count > 0:
                time.sleep(RETRY_DELAY)
                return is_valid_email(email, retry_count=retry_count-1)
            return email, "Temporarily blocked - Retry later", "warning", "❓"

        elif "access denied" in message_str or "not allowed" in message_str:
            return email, "Blocked by mail server", "warning", "❓"
        elif code == 550:
            return email, "Mailbox not found", "danger", "❌"
        else:
            return email, f"Unknown SMTP response: {code}", "warning", "❓"

    except socket.timeout:
        return email, "SMTP connection timed out", "warning", "❓"
    except smtplib.SMTPServerDisconnected:
        return email, "SMTP server disconnected", "warning", "❓"
    except smtplib.SMTPConnectError:
        return email, "SMTP connection error", "warning", "❓"
    except Exception as e:
        return email, f"Unknown error: {str(e)}", "warning", "❓"

def worker(email_queue):
    while True:
        email = email_queue.get()
        if email is None:
            break
        result = is_valid_email(email)
        with lock:
            results.append({
                "email": result[0],
                "status": result[1],
                "badge": result[2],
                "icon": result[3],
            })
        email_queue.task_done()

@app.route("/", methods=["GET"])
def index():
    return render_template_string(TEMPLATE)

@app.route("/verify", methods=["POST"])
def verify():
    global results
    results = []

    emails = []
    textarea_input = request.form.get("emails")
    if textarea_input:
        emails += [e.strip() for e in textarea_input.splitlines() if e.strip()]

    file = request.files.get("file")
    if file and file.filename.endswith(".csv"):
        stream = io.StringIO(file.stream.read().decode("utf-8"))
        reader = csv.reader(stream)
        for row in reader:
            for cell in row:
                if EMAIL_REGEX.match(cell.strip()):
                    emails.append(cell.strip())

    emails = list(set(emails))
    if not emails:
        return jsonify({"started": False, "total": 0})

    email_queue = queue.Queue()
    for email in emails:
        email_queue.put(email)

    threads = []
    for _ in range(10):
        t = threading.Thread(target=worker, args=(email_queue,))
        t.start()
        threads.append(t)

    def wait_for_completion():
        email_queue.join()
        for _ in threads:
            email_queue.put(None)
        for t in threads:
            t.join()

    threading.Thread(target=wait_for_completion).start()
    return jsonify({"started": True, "total": len(emails)})

@app.route("/results", methods=["GET"])
def get_results():
    return jsonify(results)

@app.route("/download", methods=["GET"])
def download():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Email", "Status"])
    for row in results:
        writer.writerow([row["email"], row["status"]])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype="text/csv", as_attachment=True, download_name="results.csv")

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Email Verifier</title>
    <style>
        body {
            font-family: 'Segoe UI', sans-serif;
            background: url('/static/bg.png') no-repeat center center fixed;
            background-size: cover;
            margin: 0;
            padding: 40px;
        }
        .container {
            max-width: 800px;
            margin: auto;
            background: rgba(255, 255, 255, 0.92);
            padding: 30px 40px;
            border-radius: 8px;
            box-shadow: 0 2px 15px rgba(0,0,0,0.2);
        }
        h1 { text-align: center; color: #333; }
        textarea, input[type="file"] {
            width: 100%;
            padding: 10px;
            font-size: 16px;
            margin-top: 10px;
        }
        button {
            padding: 12px 24px;
            font-size: 16px;
            margin-top: 20px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        button:hover { background: #0056b3; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 30px;
            background: white;
        }
        th, td {
            padding: 12px;
            border: 1px solid #ddd;
            text-align: left;
        }
        .success { color: green; }
        .danger { color: red; }
        .warning { color: orange; }
        #downloadBtn {
            display: none;
            margin-left: 10px;
            background: green;
        }
        #downloadBtn:hover { background: darkgreen; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📧 Bulk Email Verifier</h1>
        <form id="verifyForm">
            <label>Paste emails (one per line):</label>
            <textarea name="emails" placeholder="example@email.com"></textarea>
            <br><br>
            <label>Or upload CSV:</label>
            <input type="file" name="file" accept=".csv">
            <br><br>
            <button type="submit">Start Verifying</button>
            <button id="downloadBtn" onclick="window.location='/download'" type="button">⬇️ Download CSV</button>
        </form>
        <div id="status" style="margin-top: 20px; font-weight: bold;"></div>
        <table id="resultsTable" style="display:none;">
            <thead><tr><th>Email</th><th>Status</th></tr></thead>
            <tbody></tbody>
        </table>
    </div>

    <script>
        const form = document.getElementById("verifyForm");
        const statusDiv = document.getElementById("status");
        const resultsTable = document.getElementById("resultsTable");
        const tbody = resultsTable.querySelector("tbody");
        const downloadBtn = document.getElementById("downloadBtn");
        let resultsInterval;
        let totalExpected = 0;

        form.addEventListener("submit", function (e) {
            e.preventDefault();
            const formData = new FormData(form);
            statusDiv.textContent = "⏳ Verifying...";
            tbody.innerHTML = "";
            resultsTable.style.display = "table";
            downloadBtn.style.display = "none";

            fetch("/verify", { method: "POST", body: formData })
                .then(res => res.json())
                .then(data => {
                    if (data.started) {
                        totalExpected = data.total;
                        resultsInterval = setInterval(() => {
                            fetch("/results")
                                .then(res => res.json())
                                .then(data => {
                                    tbody.innerHTML = "";
                                    data.forEach(row => {
                                        const tr = document.createElement("tr");
                                        tr.innerHTML = `<td>${row.email}</td><td class="${row.badge}">${row.icon} ${row.status}</td>`;
                                        tbody.appendChild(tr);
                                    });

                                    if (data.length === totalExpected) {
                                        clearInterval(resultsInterval);
                                        statusDiv.textContent = "✅ Done!";
                                        downloadBtn.style.display = "inline-block";
                                    }
                                });
                        }, 2000);
                    } else {
                        statusDiv.textContent = "⚠️ No emails to verify.";
                    }
                });
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(debug=True)
