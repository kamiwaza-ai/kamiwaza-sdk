import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Matcher regex serialization is part of the relocation contract. The canary
// asserts this header appears on pages but not on excluded routes or static
// chunks.
export function middleware(_request: NextRequest) {
    const response = NextResponse.next();
    response.headers.set("x-kz-canary-middleware", "matched");
    return response;
}

export const config = {
    matcher: ["/((?!_next/static|_next/image|favicon.ico|excluded).*)"],
};
