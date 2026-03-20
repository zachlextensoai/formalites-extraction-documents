export async function POST(request: Request) {
  try {
    const body = await request.json();
    const res = await fetch("http://localhost:8000/api/fields/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}
