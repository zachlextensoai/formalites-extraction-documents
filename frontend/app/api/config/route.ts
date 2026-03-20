export async function GET() {
  try {
    const res = await fetch("http://localhost:8000/api/config");
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}
