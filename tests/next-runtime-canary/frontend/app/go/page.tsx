import { redirect } from "next/navigation";

// Static redirect: emits a .meta file whose location carries the base path
// — the relocation kind Codex's edge scan proved necessary (B4).
export default function Go(): never {
    redirect("/nested");
}
