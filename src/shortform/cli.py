"""명령줄 도구: shortform new / run / status / approve."""

from __future__ import annotations

import typer

from . import pipeline
from .config import load_settings

app = typer.Typer(help="숏폼공장: 주제 하나로 쇼츠 영상 만들기", no_args_is_help=True)


@app.command()
def new(topic: str = typer.Argument(..., help="주제 힌트, 예: '하품은 왜 옮을까'")) -> None:
    """새 작업 폴더를 만듭니다."""
    job = pipeline.Job.create(topic, load_settings())
    typer.echo(job.state.job_id)


@app.command()
def run(
    job_id: str,
    start: str = typer.Option(None, "--from", help="이 단계부터 실행"),
    stop: str = typer.Option(None, "--to", help="이 단계까지 실행"),
) -> None:
    """작업을 단계별로 실행합니다. 기본은 검수 직전(render)까지."""
    job = pipeline.Job.load(job_id, load_settings())
    stages = pipeline.default_stages()
    if stop is None and not job.state.approved:
        stop = "render"
    try:
        pipeline.run(job, stages, start=start, stop=stop)
    except pipeline.ApprovalRequired as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    except Exception as e:
        typer.echo(f"실패: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo("완료")


@app.command()
def approve(job_id: str) -> None:
    """검수를 통과시킵니다. 이후 run --from upload 로 업로드할 수 있어요."""
    job = pipeline.Job.load(job_id, load_settings())
    job.state.approved = True
    job.save_state()
    typer.echo("승인했어요")


@app.command()
def status(job_id: str) -> None:
    """단계별 진행 상태를 보여 줍니다."""
    job = pipeline.Job.load(job_id, load_settings())
    for stage in pipeline.default_stages():
        mark = job.state.stages.get(stage.name, "pending")
        err = job.state.errors.get(stage.name, "")
        typer.echo(f"{stage.name:8} {mark:8} {err}")
    typer.echo(f"검수 승인: {'예' if job.state.approved else '아니오'}")


if __name__ == "__main__":
    app()
