/** React Flight-aware byte relocation for .rsc and prerendered HTML. */

const NEWLINE_BYTE = 0x0a;
const COLON_BYTE = 0x3a;
const COMMA_BYTE = 0x2c;
const FLIGHT_PUSH_MARKER = "self.__next_f.push(";

/** Byte-level replacement preserving all non-needle bytes exactly. */
export function replaceBuffer(buffer, needle, replacement) {
    const chunks = [];
    let start = 0;
    let index = buffer.indexOf(needle);
    let occurrences = 0;
    while (index !== -1) {
        chunks.push(buffer.subarray(start, index), replacement);
        occurrences += 1;
        start = index + needle.length;
        index = buffer.indexOf(needle, start);
    }
    chunks.push(buffer.subarray(start));
    return { buffer: Buffer.concat(chunks), occurrences };
}

function relocationPairs(sentinel, replacement) {
    const encodedSentinel = encodeURIComponent(sentinel);
    const encodedReplacement = encodeURIComponent(replacement);
    return [
        [Buffer.from(sentinel, "utf8"), Buffer.from(replacement, "utf8")],
        [Buffer.from(encodedSentinel, "utf8"), Buffer.from(encodedReplacement, "utf8")],
        [
            Buffer.from(encodedSentinel.replaceAll("%2F", "%2f"), "utf8"),
            Buffer.from(encodedReplacement.replaceAll("%2F", "%2f"), "utf8"),
        ],
    ];
}

function includesRelocationPair(buffer, pairs) {
    return pairs.some(([needle]) => buffer.includes(needle));
}

function replaceRelocationPairs(buffer, pairs) {
    let output = buffer;
    let occurrences = 0;
    for (const [needle, replacement] of pairs) {
        const result = replaceBuffer(output, needle, replacement);
        output = result.buffer;
        occurrences += result.occurrences;
    }
    return { buffer: output, occurrences };
}

/** Relocate literal and percent-encoded base-path spellings. */
export function replaceRelocationForms(buffer, sentinel, replacement) {
    return replaceRelocationPairs(buffer, relocationPairs(sentinel, replacement));
}

function isAsciiHex(byte) {
    if (byte >= 0x30 && byte <= 0x39) {
        return true;
    }
    return byte >= 0x61 && byte <= 0x66;
}

function isAsciiLetter(byte) {
    if (byte >= 0x41 && byte <= 0x5a) {
        return true;
    }
    return byte >= 0x61 && byte <= 0x7a;
}

function scanHex(buffer, start) {
    let cursor = start;
    while (cursor < buffer.length && isAsciiHex(buffer[cursor])) {
        cursor += 1;
    }
    return cursor;
}

function parseLengthFrame(buffer, tagOffset) {
    const tagByte = buffer[tagOffset];
    if (!isAsciiLetter(tagByte)) {
        return null;
    }
    const hexEnd = scanHex(buffer, tagOffset + 1);
    if (hexEnd === tagOffset + 1 || buffer[hexEnd] !== COMMA_BYTE) {
        return null;
    }
    const length = Number.parseInt(
        buffer.subarray(tagOffset + 1, hexEnd).toString("latin1"),
        16,
    );
    const payloadStart = hexEnd + 1;
    const payloadEnd = payloadStart + length;
    if (!Number.isSafeInteger(length) || payloadEnd > buffer.length) {
        return null;
    }
    return {
        tag: String.fromCharCode(tagByte),
        tagOffset,
        payloadStart,
        payloadEnd,
    };
}

function parseFlightRow(buffer, start) {
    const colonOffset = scanHex(buffer, start);
    // Hint rows (`:HL[...]`) have an empty id, so a bare ':' is valid.
    if (buffer[colonOffset] !== COLON_BYTE) {
        return null;
    }
    const tagOffset = colonOffset + 1;
    const frame = parseLengthFrame(buffer, tagOffset);
    if (frame !== null) {
        return { kind: "frame", start, end: frame.payloadEnd, ...frame };
    }
    const newline = buffer.indexOf(NEWLINE_BYTE, tagOffset);
    const end = newline === -1 ? buffer.length : newline + 1;
    return { kind: "line", start, end };
}

function transformFlightRow(buffer, row, pairs) {
    const original = buffer.subarray(row.start, row.end);
    if (!includesRelocationPair(original, pairs)) {
        return original;
    }
    if (row.kind === "line") {
        return replaceRelocationPairs(original, pairs).buffer;
    }
    if (row.tag !== "T") {
        throw new Error(
            `sentinel inside unsupported length-framed Flight row type ` +
                `${JSON.stringify(row.tag)}; refusing to relocate Flight data`,
        );
    }
    const payload = buffer.subarray(row.payloadStart, row.payloadEnd);
    const replaced = replaceRelocationPairs(payload, pairs).buffer;
    return Buffer.concat([
        buffer.subarray(row.start, row.tagOffset),
        Buffer.from(`T${replaced.length.toString(16)},`, "latin1"),
        replaced,
    ]);
}

/** Relocate a complete React Flight byte stream with frame-length awareness. */
export function transformRscBuffer(buffer, sentinel, replacement) {
    const pairs = relocationPairs(sentinel, replacement);
    if (!includesRelocationPair(buffer, pairs)) {
        return buffer;
    }
    const output = [];
    let position = 0;
    while (position < buffer.length) {
        const row = parseFlightRow(buffer, position);
        if (row === null) {
            throw new Error(
                "unparseable Flight row containing the sentinel; refusing to relocate Flight data",
            );
        }
        output.push(transformFlightRow(buffer, row, pairs));
        position = row.end;
    }
    const result = Buffer.concat(output);
    if (includesRelocationPair(result, pairs)) {
        throw new Error("residual sentinel after Flight relocation; refusing to start");
    }
    return result;
}

function consumeJsonStringCharacter(state, char) {
    if (state.escaped) {
        state.escaped = false;
        return;
    }
    if (char === "\\") {
        state.escaped = true;
        return;
    }
    if (char === '"') {
        state.inString = false;
    }
}

function findJsonArrayEnd(text, start) {
    const state = { depth: 0, inString: false, escaped: false };
    for (let index = start; index < text.length; index += 1) {
        const char = text[index];
        if (state.inString) {
            consumeJsonStringCharacter(state, char);
            continue;
        }
        if (char === '"') {
            state.inString = true;
            continue;
        }
        if (char === "[") {
            state.depth += 1;
            continue;
        }
        if (char !== "]") {
            continue;
        }
        state.depth -= 1;
        if (state.depth === 0) {
            return index + 1;
        }
    }
    throw new Error("unterminated inline Flight push in prerendered HTML");
}

function isFlightDataPush(value) {
    if (!Array.isArray(value)) {
        return false;
    }
    if (value[0] !== 1) {
        return false;
    }
    return typeof value[1] === "string";
}

function findInlineScript(text, searchFrom) {
    const scriptOpen = text.indexOf("<script", searchFrom);
    if (scriptOpen === -1) {
        return null;
    }
    const openEnd = text.indexOf(">", scriptOpen + "<script".length);
    const bodyStart = openEnd + 1;
    const bodyEnd = openEnd === -1 ? -1 : text.indexOf("</script", bodyStart);
    if (openEnd === -1 || bodyEnd === -1) {
        throw new Error("unterminated script tag in prerendered HTML");
    }
    return {
        bodyStart,
        bodyEnd,
        nextSearchFrom: bodyEnd + "</script".length,
    };
}

function relocationTextForms(sentinel) {
    const encoded = encodeURIComponent(sentinel);
    return [sentinel, encoded, encoded.replaceAll("%2F", "%2f")];
}

function includesRelocationText(text, forms) {
    return forms.some((form) => text.includes(form));
}

function assertSkippedFlightScriptSafe(text, bodyStart, bodyEnd, forms) {
    const body = text.slice(bodyStart, bodyEnd);
    if (body.includes("__next_f") && includesRelocationText(body, forms)) {
        throw new Error(
            "unrecognized inline Flight push contains the relocation sentinel; refusing to patch HTML",
        );
    }
}

function parseFlightPushScript(text, bodyStart, bodyEnd, forms) {
    const marker = text.indexOf(FLIGHT_PUSH_MARKER, bodyStart);
    const markerIsScriptStart =
        marker !== -1 && marker < bodyEnd && text.slice(bodyStart, marker).trim() === "";
    // Next emits each data push as the complete body of an inline script.
    // Do not interpret rendered documentation, JSON-LD, or arbitrary author
    // scripts that merely contain the marker text as Flight data.
    if (!markerIsScriptStart) {
        assertSkippedFlightScriptSafe(text, bodyStart, bodyEnd, forms);
        return null;
    }
    const jsonStart = marker + FLIGHT_PUSH_MARKER.length;
    if (text[jsonStart] !== "[") {
        throw new Error("inline Flight push must contain a JSON array");
    }
    const jsonEnd = findJsonArrayEnd(text, jsonStart);
    if (jsonEnd > bodyEnd) {
        throw new Error("inline Flight push crosses its script boundary");
    }
    const value = JSON.parse(text.slice(jsonStart, jsonEnd));
    // A second data push in the same script is not part of the stream we
    // reassemble below. Refuse any trailing sentinel rather than letting the
    // final whole-document replacement corrupt its Flight frame length.
    assertSkippedFlightScriptSafe(text, jsonEnd, bodyEnd, forms);
    if (!isFlightDataPush(value)) {
        return null;
    }
    return { jsonStart, jsonEnd, value };
}

function collectFlightPushes(text, sentinel) {
    const pushes = [];
    const forms = relocationTextForms(sentinel);
    let searchFrom = 0;
    for (let script = findInlineScript(text, searchFrom); script !== null; ) {
        const push = parseFlightPushScript(
            text,
            script.bodyStart,
            script.bodyEnd,
            forms,
        );
        if (push !== null) {
            pushes.push(push);
        }
        searchFrom = script.nextSearchFrom;
        script = findInlineScript(text, searchFrom);
    }
    return pushes;
}

function advanceUtf8Boundary(buffer, offset) {
    let boundary = offset;
    while (boundary < buffer.length && (buffer[boundary] & 0xc0) === 0x80) {
        boundary += 1;
    }
    return boundary;
}

function splitFlightBuffer(buffer, originalByteLengths) {
    const chunks = [];
    let originalBoundary = 0;
    let outputBoundary = 0;
    for (const length of originalByteLengths.slice(0, -1)) {
        originalBoundary += length;
        const candidate = Math.min(originalBoundary, buffer.length);
        const nextBoundary = advanceUtf8Boundary(buffer, candidate);
        chunks.push(buffer.subarray(outputBoundary, nextBoundary).toString("utf8"));
        outputBoundary = nextBoundary;
    }
    chunks.push(buffer.subarray(outputBoundary).toString("utf8"));
    return chunks;
}

function stringifyForInlineScript(value) {
    return JSON.stringify(value)
        .replace(/</g, "\\u003c")
        .replace(/>/g, "\\u003e")
        .replace(/&/g, "\\u0026")
        .replace(/\u2028/g, "\\u2028")
        .replace(/\u2029/g, "\\u2029");
}

function replaceFlightPushes(text, pushes, chunks) {
    let output = "";
    let position = 0;
    for (let index = 0; index < pushes.length; index += 1) {
        const push = pushes[index];
        push.value[1] = chunks[index];
        output += text.slice(position, push.jsonStart);
        output += stringifyForInlineScript(push.value);
        position = push.jsonEnd;
    }
    return output + text.slice(position);
}

/** Relocate HTML plus its chunked inline React Flight stream. */
export function transformHtmlBuffer(buffer, sentinel, replacement) {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    const pushes = collectFlightPushes(text, sentinel);
    const flight = Buffer.from(pushes.map((push) => push.value[1]).join(""), "utf8");
    let transformedHtml = text;
    const pairs = relocationPairs(sentinel, replacement);
    if (includesRelocationPair(flight, pairs)) {
        const transformedFlight = transformRscBuffer(flight, sentinel, replacement);
        const byteLengths = pushes.map((push) => Buffer.byteLength(push.value[1], "utf8"));
        const chunks = splitFlightBuffer(transformedFlight, byteLengths);
        transformedHtml = replaceFlightPushes(text, pushes, chunks);
    }
    return replaceRelocationPairs(Buffer.from(transformedHtml, "utf8"), pairs).buffer;
}
