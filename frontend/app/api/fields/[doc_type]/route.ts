export async function GET(
  _request: Request,
  { params }: { params: Promise<{ doc_type: string }> }
) {
  const { doc_type } = await params;
  try {
    const res = await fetch(
      `http://localhost:8000/api/fields/${encodeURIComponent(doc_type)}`
    );
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}
