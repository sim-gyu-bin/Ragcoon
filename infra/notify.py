"""배포 상태만 승인된 개인 대화에 보내고, 오류 원문은 로그에 남기지 않는다."""

import http.client
import json
import os
import re
import sys


STAGES = ("gate", "check", "build", "deploy")
LABELS = {"success": "성공", "failure": "실패", "cancelled": "취소", "skipped": "미실행"}
RESPONSE_LIMIT = 65536


def build_message(env):
    revision = env["GITHUB_SHA"]
    run_id = env["GITHUB_RUN_ID"]
    attempt = env["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not all(
        re.fullmatch(r"[1-9][0-9]{0,19}", number) for number in (run_id, attempt)
    ):
        raise ValueError("실행 식별자가 올바르지 않습니다.")

    results = [env[f"{stage.upper()}_RESULT"] for stage in STAGES]
    if any(result not in LABELS for result in results):
        raise ValueError("알 수 없는 단계 상태입니다.")
    # 실패 뒤 건너뛴 단계가 있어도 전체는 실패다. 복구를 포함한 모든 단계가 끝나야 성공이다.
    overall = next((state for state in ("failure", "cancelled", "skipped") if state in results), "success")
    stages = ", ".join(f"{stage}: {LABELS[result]}" for stage, result in zip(STAGES, results))
    return "\n".join((
        f"RAGcoon 배포 시험: {LABELS[overall]}", stages, f"SHA: {revision[:12]}",
        f"https://github.com/sim-gyu-bin/Ragcoon/actions/runs/{run_id}/attempts/{attempt}",
    ))


def send_message(token, chat_id, message):
    if not re.fullmatch(r"[0-9]{1,20}:[A-Za-z0-9_-]{20,128}", token):
        raise ValueError("봇 토큰 형식이 올바르지 않습니다.")
    if not re.fullmatch(r"[1-9][0-9]{0,15}", chat_id) or int(chat_id) >= 2**52:
        raise ValueError("개인 대화의 ID가 아닙니다.")
    body = json.dumps({
        "chat_id": chat_id, "text": message, "link_preview_options": {"is_disabled": True},
    }, ensure_ascii=False).encode("utf-8")
    # 기본 인증서 검증을 사용하고 토큰이 든 경로를 redirect 대상으로 전달하지 않는다.
    connection = http.client.HTTPSConnection("api.telegram.org", timeout=10)
    try:
        connection.request("POST", f"/bot{token}/sendMessage", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Telegram 전송 실패")
        raw = response.read(RESPONSE_LIMIT + 1)
        if len(raw) > RESPONSE_LIMIT:
            raise ValueError("응답 크기 초과")
        receipt = json.loads(raw)
        if not isinstance(receipt, dict) or receipt.get("ok") is not True:
            raise ValueError("Telegram 전송 실패")
    finally:
        connection.close()


def main():
    try:
        send_message(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], build_message(os.environ))
    except (KeyError, ValueError, OSError, http.client.HTTPException):
        # 예외·URL·응답에는 토큰과 대화 ID가 있을 수 있다. 배포 결과와 별도인 경고만 출력한다.
        print("::warning::Telegram 알림을 전달하지 못했습니다. 비밀 설정과 연결 상태를 확인하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
