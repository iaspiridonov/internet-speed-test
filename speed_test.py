#!/usr/bin/env python3
"""Measure HTTP download throughput with ten sequential requests."""

import argparse
import math
import sys
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024
MIB = 1024 * 1024


def http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError
        parsed.port  # Validate an explicitly supplied port as well.
    except ValueError:
        raise argparse.ArgumentTypeError("нужен корректный URL с http:// или https://")
    return value


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("тайм-аут должен быть положительным числом")
    return seconds


def download(url: str, timeout: float) -> tuple[int, float]:
    request = Request(
        url,
        headers={
            "User-Agent": "internet-speed-test/1.0",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache, no-store",
        },
    )
    downloaded = 0
    started = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            downloaded += len(chunk)

        # Sized reads can reach EOF without raising on a truncated body.
        content_length = response.headers.get("Content-Length")
        if response.headers.get("Transfer-Encoding") is None and content_length is not None:
            if downloaded != int(content_length):
                raise ValueError("ответ оборвался: объём не совпадает с Content-Length")
    return downloaded, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Скорость скачивания по 10 последовательным HTTP-запросам."
    )
    parser.add_argument("url", type=http_url, help="прямая ссылка на скачиваемый ресурс")
    parser.add_argument(
        "--timeout", type=positive_seconds, default=30.0,
        help="тайм-аут сетевой операции в секундах (по умолчанию: 30)",
    )
    args = parser.parse_args()
    total_bytes = 0
    total_seconds = 0.0

    try:
        for number in range(1, REQUEST_COUNT + 1):
            size, elapsed = download(args.url, args.timeout)
            total_bytes += size
            total_seconds += elapsed
            print(f"Запрос {number:2}/{REQUEST_COUNT}: {size} байт, {elapsed:.3f} с", flush=True)
    except HTTPError as error:
        error.close()
        print(f"Ошибка запроса {number}: HTTP {error.code}. Замер не завершён.", file=sys.stderr)
        return 1
    except (URLError, OSError, HTTPException, ValueError) as error:
        print(f"Ошибка запроса {number}: {error}. Замер не завершён.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nЗамер прерван пользователем.", file=sys.stderr)
        return 130

    average_seconds = total_seconds / REQUEST_COUNT
    speed = total_bytes / total_seconds / MIB if total_seconds > 0 else 0.0
    print(f"\nСреднее время запроса: {average_seconds:.3f} с")
    print(f"Всего скачано: {total_bytes} байт ({total_bytes / MIB:.3f} MiB)")
    print(f"Общее время запросов: {total_seconds:.3f} с")
    print(f"Скорость скачивания: {speed:.3f} MiB/s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
