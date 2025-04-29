# JetKey
jetKVM RPC client that is able to send keyboard input to jetkvm.
Idea is inspired by [jetkvm_control](https://github.com/davehorner/jetkvm_control) from @davehorner.
It utilizes the hew WebRTC signalling protocol recently implemented by JetKVM.

## MIT License
Copyright (c) 2025 David Horner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Usage
Install the dependencies specified in the requirements.txt e.g. pip install -r requirements.txt
Then put `jetkey.py` in your project directory, and call it like show in the following example.
This example shows how to select the next available display projcetion mode:
```python
import jetkey

host: str = "192.168.1.42"
port: int = 80
password: str = "correct horse battery staple"

jetkey: JetKey
async with JetKey(host, port, password) as jetkey:
    key_combinations_to_send: List[KeyCombo] = [
        KeyCombo(modifiers={0x08}, keys={0x13}, hold=100, wait=500),# projection modes
        KeyCombo(modifiers={0x00}, keys={0x13}, hold=100, wait=500),# switch one down
        KeyCombo(modifiers={0x00}, keys={0x28}, hold=100, wait=200),# hit enter
        KeyCombo(modifiers={0x00}, keys={0x29}, hold=100, wait=200),# hit escape
        KeyCombo(
            modifiers={0x00}, keys={0x00}, clear_keys=True, wait=100
        ),                                                          # Release all keys
    ]
    await jetkey.send_key_combinations(key_combinations_to_send)
    await asyncio.sleep(0.1)

```

## Keymappings
Keymappings can be found at [JetKVM - keyboardMappings](https://github.com/jetkvm/kvm/blob/dev/ui/src/keyboardMappings.ts)
