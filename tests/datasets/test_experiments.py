from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import nest_asyncio
from phoenix.datasets.evaluators import (
    ConcisenessEvaluator,
    ContainsKeyword,
    HelpfulnessEvaluator,
)
from phoenix.datasets.evaluators.utils import create_evaluator
from phoenix.datasets.experiments import run_experiment
from phoenix.datasets.types import (
    AnnotatorKind,
    CanAsyncEvaluate,
    CanEvaluate,
    Dataset,
    Example,
    ExperimentResult,
    ExperimentRun,
    JSONSerializable,
)
from phoenix.db import models
from phoenix.server.api.types.node import from_global_id_with_expected_type
from sqlalchemy import select
from strawberry.relay import GlobalID


@patch("opentelemetry.sdk.trace.export.SimpleSpanProcessor.on_end")
async def test_run_experiment(_, session, sync_test_client, simple_dataset):
    nest_asyncio.apply()

    nonexistent_experiment = (await session.execute(select(models.Experiment))).scalar()
    assert not nonexistent_experiment, "There should be no experiments in the database"

    test_dataset = Dataset(
        id=str(GlobalID("Dataset", "0")),
        version_id=str(GlobalID("DatasetVersion", "0")),
        examples=[
            Example(
                id=str(GlobalID("DatasetExample", "0")),
                input={"input": "fancy input 1"},
                output={},
                metadata={},
                updated_at=datetime.now(timezone.utc),
            )
        ],
    )

    with patch("phoenix.datasets.experiments._phoenix_client", return_value=sync_test_client):

        def experiment_task(example: Example) -> str:
            return "doesn't matter, this is the output"

        experiment = run_experiment(
            dataset=test_dataset,
            task=experiment_task,
            experiment_name="test",
            experiment_description="test description",
            # repetitions=3, TODO: Enable repetitions #3584
            evaluators=[
                ContainsKeyword(keyword="correct"),
                ContainsKeyword(keyword="doesn't matter"),
            ],
        )
        experiment_id = from_global_id_with_expected_type(
            GlobalID.from_id(experiment.id), "Experiment"
        )
        assert experiment_id

        experiment_model = (await session.execute(select(models.Experiment))).scalar()
        assert experiment_model, "An experiment was run"
        assert experiment_model.dataset_id == 0
        assert experiment_model.dataset_version_id == 0
        assert experiment_model.name == "test"
        assert experiment_model.description == "test description"
        assert experiment_model.repetitions == 1  # TODO: Enable repetitions #3584

        experiment_runs = (
            (
                await session.execute(
                    select(models.ExperimentRun).where(models.ExperimentRun.dataset_example_id == 0)
                )
            )
            .scalars()
            .all()
        )
        assert len(experiment_runs) == 1, "The experiment was configured to have 1 repetition"
        for run in experiment_runs:
            assert run.output == {"result": "doesn't matter, this is the output"}

            evaluations = (
                (
                    await session.execute(
                        select(models.ExperimentRunAnnotation).where(
                            models.ExperimentRunAnnotation.experiment_run_id == run.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(evaluations) == 2
            assert evaluations[0].score == 0.0
            assert evaluations[1].score == 1.0


@patch("opentelemetry.sdk.trace.export.SimpleSpanProcessor.on_end")
async def test_run_experiment_with_llm_eval(_, session, sync_test_client, simple_dataset):
    nest_asyncio.apply()

    nonexistent_experiment = (await session.execute(select(models.Experiment))).scalar()
    assert not nonexistent_experiment, "There should be no experiments in the database"

    test_dataset = Dataset(
        id=str(GlobalID("Dataset", "0")),
        version_id=str(GlobalID("DatasetVersion", "0")),
        examples=[
            Example(
                id=str(GlobalID("DatasetExample", "0")),
                input={"input": "fancy input 1"},
                output={},
                metadata={},
                updated_at=datetime.now(timezone.utc),
            )
        ],
    )

    class PostitiveFakeLLMModel:
        model_name = "fake-llm"

        def _generate(self, prompt: str, **kwargs: Any) -> str:
            return " doesn't matter I can't think!\nLABEL: true"

        async def _async_generate(self, prompt: str, **kwargs: Any) -> str:
            return " doesn't matter I can't think!\nLABEL: true"

    class NegativeFakeLLMModel:
        model_name = "fake-llm"

        def _generate(self, prompt: str, **kwargs: Any) -> str:
            return " doesn't matter I can't think!\nLABEL: false"

        async def _async_generate(self, prompt: str, **kwargs: Any) -> str:
            return " doesn't matter I can't think!\nLABEL: false"

    with patch("phoenix.datasets.experiments._phoenix_client", return_value=sync_test_client):

        def experiment_task(input):
            return "doesn't matter, this is the output"

        experiment = run_experiment(
            dataset=test_dataset,
            task=experiment_task,
            experiment_name="test",
            experiment_description="test description",
            # repetitions=3,  # TODO: Enable repetitions #3584
            evaluators=[
                ConcisenessEvaluator(model=NegativeFakeLLMModel()),
                HelpfulnessEvaluator(model=PostitiveFakeLLMModel()),
            ],
        )
        experiment_id = from_global_id_with_expected_type(
            GlobalID.from_id(experiment.id), "Experiment"
        )
        assert experiment_id

        experiment_model = (await session.execute(select(models.Experiment))).scalar()
        assert experiment_model, "An experiment was run"
        assert experiment_model.dataset_id == 0
        assert experiment_model.dataset_version_id == 0
        assert experiment_model.name == "test"
        assert experiment_model.description == "test description"

        experiment_runs = (
            (
                await session.execute(
                    select(models.ExperimentRun).where(models.ExperimentRun.dataset_example_id == 0)
                )
            )
            .scalars()
            .all()
        )
        assert len(experiment_runs) == 1, "The experiment was configured to have 1 repetition"
        for run in experiment_runs:
            assert run.output == {"result": "doesn't matter, this is the output"}

        for run in experiment_runs:
            evaluations = (
                (
                    await session.execute(
                        select(models.ExperimentRunAnnotation).where(
                            models.ExperimentRunAnnotation.experiment_run_id == run.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(evaluations) == 2
            assert evaluations[0].score == 0.0
            assert evaluations[1].score == 1.0


def test_evaluator_decorator():
    @create_evaluator()
    def can_i_count_this_high(x: int) -> bool:
        return x < 3

    assert can_i_count_this_high(3) is False
    assert can_i_count_this_high(2) is True
    assert isinstance(can_i_count_this_high, CanEvaluate)
    assert can_i_count_this_high.name == "can_i_count_this_high"
    assert can_i_count_this_high.annotator_kind == AnnotatorKind.CODE.value


async def test_async_evaluator_decorator():
    @create_evaluator(name="override", kind="LLM")
    async def can_i_count_this_high(x: int) -> bool:
        return x < 3

    assert await can_i_count_this_high(3) is False
    assert await can_i_count_this_high(2) is True
    assert isinstance(can_i_count_this_high, CanAsyncEvaluate)
    assert can_i_count_this_high.name == "override"
    assert can_i_count_this_high.annotator_kind == AnnotatorKind.LLM.value


def test_binding_arguments_to_decorated_evaluators():
    example = Example(
        id="1",
        input="the biggest number I know",
        output=99,
        metadata={"data": "there's nothing here"},
        updated_at=datetime.now(timezone.utc),
    )
    experiment_run = ExperimentRun(
        start_time=datetime.now(timezone.utc),
        end_time=datetime.now(timezone.utc),
        experiment_id=1,
        dataset_example_id=1,
        repetition_number=1,
        output=ExperimentResult(result=3),
    )

    @create_evaluator()
    def can_i_count_this_high(x: int) -> bool:
        return x == 3

    @create_evaluator()
    def can_i_evaluate_the_output(output: int) -> bool:
        return output == 3

    @create_evaluator()
    def can_i_evaluate_the_reference(reference: int) -> bool:
        return reference == 99

    @create_evaluator()
    def can_i_evaluate_the_input(input: str) -> bool:
        return input == "the biggest number I know"

    @create_evaluator()
    def can_i_evaluate_using_metadata(
        metadata: JSONSerializable,
    ) -> bool:
        return metadata == {"data": "there's nothing here"}

    @create_evaluator()
    def can_i_evaluate_with_everything(
        input: str, output: int, reference: int, metadata: JSONSerializable
    ) -> bool:
        check_input = input == "the biggest number I know"
        check_output = output == 3
        check_reference = reference == 99
        check_metadata = metadata == {"data": "there's nothing here"}
        return check_input and check_output and check_reference and check_metadata

    @create_evaluator()
    def can_i_evaluate_with_everything_in_any_order(
        reference: int, output: int, metadata: JSONSerializable, input: str
    ) -> bool:
        check_input = input == "the biggest number I know"
        check_output = output == 3
        check_reference = reference == 99
        check_metadata = metadata == {"data": "there's nothing here"}
        return check_input and check_output and check_reference and check_metadata

    evaluation = can_i_count_this_high.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "With one argument, evaluates against output.result"

    evaluation = can_i_evaluate_the_output.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "With output arg, evaluates against output.result"

    evaluation = can_i_evaluate_the_reference.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "With reference arg, evaluates against example.output"

    evaluation = can_i_evaluate_the_input.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "With input arg, evaluates against example.input"

    evaluation = can_i_evaluate_using_metadata.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "With metadata arg, evaluates against example.metadata"

    evaluation = can_i_evaluate_with_everything.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "evaluates against named args in any order"

    evaluation = can_i_evaluate_with_everything_in_any_order.evaluate(experiment_run, example)
    assert evaluation.score == 1.0, "evaluates against named args in any order"