"""Internal alignment command helpers, shared through the ``commands.align`` facade.

``reads`` owns read-source classification, input validation and length sampling.
``selection`` owns backend choice, compatibility checks and preset resolution.
``output`` owns streamed output publication, index staging and validation.
"""
