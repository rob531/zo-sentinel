"""Deterministic build gates shared by the builder, the publisher and CI.

A gate that exists in only one of those three places is a gate the loop can
walk around. Each module here is the SINGLE definition of one rejection rule;
every consumer imports it rather than restating it.
"""