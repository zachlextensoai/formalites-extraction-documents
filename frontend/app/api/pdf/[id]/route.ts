import { NextRequest } from "next/server";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const res = await fetch(`http://localhost:8000/api/pdf/${id}`);

  if (!res.ok) {
    return new Response("PDF not found", { status: res.status });
  }

  const blob = await res.blob();
  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `inline; filename="${id}.pdf"`,
    },
  });
}
