import subprocess

def poseidon_hash(inputs):
    if len(inputs) != 1:
        raise ValueError("Chỉ hỗ trợ 1 input")
    x_hex = hex(inputs[0])[2:] if isinstance(inputs[0], int) else inputs[0].lstrip("0x")
    js_code = f"""
    const circomlibjs = require('circomlibjs');
    (async () => {{
        const poseidon = await circomlibjs.buildPoseidon();
        const arg = process.argv[1];
        const v = BigInt(arg);
        const out = poseidon([v]);
        console.log(poseidon.F.toString(out));
    }})();
    """
    out = subprocess.check_output(
        ['node', '-e', js_code, str(inputs[0])],
        text=True
    )
    return int(out.strip())
