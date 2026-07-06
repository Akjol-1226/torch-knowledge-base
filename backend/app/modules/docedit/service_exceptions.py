class DoceditForbidden(RuntimeError):
    pass


class DoceditNotFound(RuntimeError):
    pass


class DoceditConflict(RuntimeError):
    pass


class DoceditBadRequest(RuntimeError):
    pass


class DoceditUnprocessableEntity(RuntimeError):
    pass


class DoceditUpstreamError(RuntimeError):
    pass


class DoceditServiceUnavailable(RuntimeError):
    pass

