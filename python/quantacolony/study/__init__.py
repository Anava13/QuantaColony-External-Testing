"""QuantaColony user study: Colony Detection on a fixed set of plates, measured by many people.

The study asks whether measurements made with Colony Detection replicate across users. Each participant
fills in a short form, reads what to measure, practices on a plate with a guided tutorial, then measures
four plates (two synthetic with known truth, two real), from easiest to hardest, in the same order for everyone. Benchmark recording
(core/benchmark.py) keeps every action with its time. The session ends with one zip file holding the
participant's project and participant.json, to send back.

    uv run quantacolony-study           the study app
    model.py                            participant, order, bundle, tutorial (Qt-free)
    project.py                          the participant's project and the results zip
    views.py                            the form, concept, tutorial panel and finish screens
    controller.py                       the flow from form to zip
    bundle/                             the plates (tools/study/build_bundle.py writes it)
"""
