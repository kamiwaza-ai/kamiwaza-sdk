"use client";

export function LongText({ text }: { text: string }) {
    return <p data-testid="long-text">{text.slice(-80)}</p>;
}
