from analysis.broadcast import Broadcaster


class Sock:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_text(self, data):
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(data)


async def test_broadcast_reaches_all_clients_and_drops_dead_ones():
    b = Broadcaster()
    good, dead = Sock(), Sock(fail=True)
    b.add(good)
    b.add(dead)
    await b.broadcast("hello")
    assert good.sent == ["hello"]
    assert b.client_count == 1  # the dead socket was removed, the good one still served
    await b.broadcast("again")
    assert good.sent == ["hello", "again"]
