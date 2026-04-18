# Gotenberg Gateway — API Examples

The Gotenberg Gateway sits as a reverse proxy in front of the [Gotenberg API](https://gotenberg.dev/), protecting it with concurrency limits, circuit breakers, and connection timeouts. Except perfectly passing through all multipart form requests to Gotenberg, the gateway introduces specific features like `X-Request-ID` tracing to help you track jobs effectively.

This guide provides practical `curl` examples for the most common operations. Note that all requests are sent to the **Gateway** (default port `9225`), which buffers, safely schedules, and securely routes them to Gotenberg.

---

## 1. Convert URL to PDF

Captures a webpage and converts it into a PDF document.

```bash
curl -X POST http://localhost:9225/forms/chromium/convert/url \
  --form url="https://example.com" \
  --form paperWidth="8.5" \
  --form paperHeight="11" \
  --form marginTop="0.5" \
  --form marginBottom="0.5" \
  --form scale="1.0" \
  -o example.pdf
```

**What happens?** The Gateway checks your IP's current limit. If there is room, it forwards the URL to Gotenberg's internal Chromium engine to render the `https://example.com` page. 

---

## 2. Convert HTML to PDF

Converts a static HTML file (along with its linked assets like CSS and images) into a PDF. 

Create a simple `index.html`:
```html
<!DOCTYPE html>
<html>
<head><title>Test Invoice</title></head>
<body>
  <h1>Invoice #1234</h1>
  <p>Total amount: $100.00</p>
</body>
</html>
```

Now, convert it:
```bash
curl -X POST http://localhost:9225/forms/chromium/convert/html \
  -F "files=@index.html" \
  -o invoice.pdf
```

### Passing Multiple Assets (HTML + CSS)
Gotenberg supports receiving multiple files seamlessly. Just upload them using multiple `-F` fields:
```bash
curl -X POST http://localhost:9225/forms/chromium/convert/html \
  -F "files=@index.html" \
  -F "files=@style.css" \
  -F "files=@logo.png" \
  -o output.pdf
```

---

## 3. Convert Markdown to PDF

Gotenberg can automatically compile Markdown files into PDFs using HTML wrappers.

```bash
curl -X POST http://localhost:9225/forms/chromium/convert/markdown \
  -F "files=@index.html" \
  -F "files=@README.md" \
  -o readme.pdf
```
> **Note:** Markdown conversion requires an entrypoint `index.html` file containing the Go template `{{ toHTML .DirPath "README.md" }}`. Consult the [Gotenberg Docs](https://gotenberg.dev/docs/routes#markdown) for template wiring.

---

## 4. Convert Office Documents (LibreOffice)

Gotenberg packs LibreOffice internally, allowing you to convert `.docx`, `.xlsx`, or `.pptx` files dynamically.

```bash
curl -X POST http://localhost:9225/forms/libreoffice/convert \
  -F "files=@report.docx" \
  -o report.pdf
```

**Bulk Conversion:** You can send multiple documents in a single request. Gotenberg will convert each document and return a ZIP file containing the resulting PDFs!

```bash
curl -X POST http://localhost:9225/forms/libreoffice/convert \
  -F "files=@report.docx" \
  -F "files=@presentation.pptx" \
  -o output.zip
```

---

## 5. Merge PDFs

The Gotenberg PDF Engines route allows you to merge multiple existing PDF files into a single master PDF.

```bash
curl -X POST http://localhost:9225/forms/pdfengines/merge \
  -F "files=@chapter1.pdf" \
  -F "files=@chapter2.pdf" \
  -o completed_book.pdf
```
*(The files are appended in the exact alphabetical order of their filenames.)*

---

## Gateway-Specific Features

By using the gateway, you also gain access to tracing, health diagnostics, and graceful retry responses.

### Endpoints
The proxy exposes administrative paths directly:

- **Check Current Queue Capacity & Your IP Status:**
  ```bash
  curl http://localhost:9225/
  ```
- **Check Circuit Breaker & Health Status:**
  ```bash
  curl http://localhost:9225/health
  ```
- **OpenAPI / Swagger Specs (Interactive):**
  Open `http://localhost:9225/docs` in your browser.

### Automatic Trace tracking (`X-Request-ID`)
To track a job across your application and the Gateway logs, you can inject an `X-Request-ID` header. If provided, the Gateway honors it and reflects it in the logs. If absent, the Gateway assigns a random UUID.

```bash
curl -X POST http://localhost:9225/forms/chromium/convert/url \
  -H "X-Request-ID: app-job-77349" \
  -F url="https://example.com" \
  -o output.pdf
```

### Understanding Rejections
When your queue defaults are met, you will instantly receive a `503 Service Unavailable`. Instead of crashing your pipeline, the Gateway guarantees swift rejection signaling you should poll / retry later.

```bash
< HTTP/1.1 503 Service Unavailable
< Retry-After: 60
< Content-Type: application/json

{"message": "Service busy. Queue is full.", "retry_after": 60}
```
