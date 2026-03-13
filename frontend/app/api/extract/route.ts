export const maxDuration = 120;

export async function POST(request: Request) {
  const body = await request.json();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);

  try {
    const res = await fetch("http://localhost:8000/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "AbortError") {
      return Response.json({ detail: "Extraction timeout" }, { status: 504 });
    }
    return Response.json({ detail: "Extraction failed" }, { status: 502 });
  } finally {
    clearTimeout(timeout);
  }
}
