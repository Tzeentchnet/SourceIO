"""Download third-party sample assets used by the headless import tests.

Usage:  python tests/fetch_samples.py [--dest samples] [--only source2,goldsrc]

Files come from public GitHub repositories, pinned to a commit, and are verified against their
git blob hash. They are third-party (largely Valve) content for local testing only: they land
in the git-ignored ``samples/`` folder and must not be committed.
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

COMMITS = {
    "ValveResourceFormat/ValveResourceFormat": "42743fb93030c552f834fabb30708dad84d0cbf5",
    "TeamSpen210/srctools": "7dfff9fb77c0abd01bdc7f9c1b93dcc6b553099f",
    "ValveSoftware/source-sdk-2013": "b8cfb12c0e083a2ef5b2f9f9b50f3902fa034474",
    "LordVonAdel/source-mdl": "2b6ff6252bffcdf0f92bc360cf1bbca459bd66c0",
    "icewind1991/vmdl": "2ebc2c8ee50c8bc80d00bad4613ceeba7bda6b0f",
    "LogicAndTrick/sledge-formats": "684fbf6829b42a09628aae8faa4879411cdfa630",
    "EngineersBox/Lambda": "ec7e4eda27d835c546e2fde3504d96977ecac4ab",
}

# (destination folder, repository, path in repository, git blob sha1, size in bytes)
FILES = [
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/alyx_hand_left.vmdl_c", "ed47686dacbef67dd5951cba59fd15483ec6e6c6", 257198),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/box_creature_model.vmdl_c", "cebec2c2acbc47d20d8575a3b93d3a176cc3a459", 81382),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/ctm_sas.vmdl_c", "aee8f7c259cd2fc992808aa0f64f13d32cba32ac", 563655),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/empty_vertex_buffer.vmdl_c", "6882fcc5a9b864bf26d4f8df8bfd825e8526c2db", 9218),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/lod_test.vmdl_c", "6f6a40637313eb055cf3f23a69dc051d3e0afc4c", 9791),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/necro_archer.vmdl_c", "a20c3382915201859ef2809980b967c707be337e", 210794),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/stone_tranquility_helm.vmdl_c", "9074fb087df4a524f3bf3fae8c283c80b6922675", 15045),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/sw_donkey_10th_anniversary_kv3_v3_zstd.vmdl_c", "cb79170014e3a929d4b5c5393992c2c2f635e749", 213499),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/townsfolk_03.vmdl_c", "6084eb5290ef0c5df35f033988fe77c72c3e2ae6", 301602),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/tube_wire_16.vmdl_c", "bdca4191af8fbb6800012727652fe0e80c1715b3", 4635),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/unnamed_15451_kv3_v5_uncompressed.vmdl_c", "68abf8768e342ba9703513f3812c63a79cf6b53a", 4016),
    ("source2/models", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/wooden_crate_01.vmdl_c", "bebe9b3db1e9c2e04f417325073f315fc459e680", 19039),
    ("source2/materials", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/compute_reactive_mask_kv3_v3_lz4.vmat_c", "1dd42252d28e164c7de055ae11e540328de5e76f", 1322),
    ("source2/materials", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/panorama_world_panel_default_kv3_v3_lz4.vmat_c", "01ad9dce48338ee7b8e4de72640bf985d9390b7d", 2594),
    ("source2/materials", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/player_start_kv3_v5.vmat_c", "329567514887c99b1ecca074584e350e276614cb", 3149),
    ("source2/materials", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/point_worldtext_default.vmat_c", "0d4131e786905d341194d8e66fb2bce1867364df", 4392),
    ("source2/materials", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/reflectivity_90b.vmat_c", "5f9ee811021f1d835ad300d27cbb078fe875f359", 4296),
    ("source2/physics", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/generic_grip.vphys_c", "fc364ea8317d36fbb168a2794a90ee688997b809", 18096),
    ("source2/physics", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/juggernaut.vphys_c", "8546a384da79c77a9e4e594f9e4df0f37d3fb625", 30824),
    ("source2/physics", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/void_spirit_ar.vphys_c", "bdd7a0ef1ff4c5ba07c628b27eb4679d870d9cb3", 1868),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/ATI2N_hotel_tarp_001_freedom_psd_993397bd.vtex_c", "e50e6410d1fba67db89c09eff2b3f6b02933c636", 153573),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/BC6H.vtex_c", "323bc6de007efc989a72484a5d13a9a7398b1246", 66641),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/BC7_testgrid_color_tga_2d6cc34.vtex_c", "ea3c3c6bac8f0198c1b4e9b99ce8093f6d564e1c", 351380),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/DXT1_dota_default_cube_tga_c95513b9.vtex_c", "3d06954094b186c298eec7d46b2abf87e97386b2", 197336),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/DXT5_lava_drops_sheet.vtex_c", "a3b1aa9c2ea6d31209610294b82541582259ce2a", 3672),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/HemiOct_dark_grey_bk_tnormal.vtex_c", "e3508def995259d2f067805c8ad4d03aaa4e19f6", 1844),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/I8.vtex_c", "743d415066ca26bff68b814c0fd73acf0b7be434", 2804),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/PNG_DXT5_gray_button_on_png.vtex_c", "61d6304ce9675352e9f47c2c4c51e4801c56048a", 8239),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/R32F.vtex_c", "f89031b742829e5e626a45cf61a0845014a62dbc", 81204),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/RG1616.vtex_c", "8f5385aef7e264b7648bdead5f65cf09d10f2159", 263940),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/RGBA16161616F.vtex_c", "f32ac2e3e56c7b0cb1cf2fa60cbf3115e744d571", 5160),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/RGBA8888_red_large_on_png.vtex_c", "bd4c8d9e07da82ca9785489ef41537eb7fea0856", 94196),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/WEBP_RGBA8888.vtex_c", "59769d818ebc8ebc4b0bf0f5411a5b7c2ee081f9", 3628),
    ("source2/textures", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/Textures/cubemap.vtex_c", "39442893c0a764619c8338f39351446b9d2eaf85", 5160),
    ("source2/maps", "ValveResourceFormat/ValveResourceFormat", "Tests/Files/small_map_with_material.vpk", "1b913740b274093716c22ad672d1c5f51aa95e43", 24700),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/hotspot.vtf", "98aea73bd306c005322045782659eb18dfe7bc2e", 349801),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_a8.vmt", "0f1c1086283e6ef452092f2e9f2207b1fca8aa6e", 89),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_a8.vtf", "a53f474caf9e33b6fcf7e06222806c760fa4e54a", 5669),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgr565.vmt", "b3931c2c99460d998b82e822191cf85cc46c609d", 93),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgr565.vtf", "457eb427fa77f491972136a5c1b119cbb994fa2a", 11130),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgr888.vmt", "6bdde598000352770fe15ed3c100e2665894b60e", 93),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgr888.vtf", "51433f40b72e55aac620a011d83b70c868fefdfd", 16591),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgra8888.vmt", "67983d15dfa35f94b4e9a650d1df1d3348d6bb53", 95),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_bgra8888.vtf", "00ff3946408efebda5938e60103eae0254ba2941", 22052),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt1.vmt", "0ed1c18846498c28f4b094d00e28719f2db28b15", 91),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt1.vtf", "f739254aaf14296dfa04ec2c8ffe9b761cdbb986", 2952),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt1_onebitalpha.vmt", "56df023a9d96fe1c2e9590352d6e28d1c4731ee0", 103),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt1_onebitalpha.vtf", "0267e2eaf463c45263f25b3b53a5fb487c414b77", 2952),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt3.vmt", "40e215e666fdb241c796271aa700fc3dae769cb2", 91),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt3.vtf", "f35bc72b6e7e31179287ff6d78e1ef8adfd5a07c", 5696),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt5.vmt", "00ffbefbdda141be7e45f346d3f85cc544fad003", 91),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_dxt5.vtf", "a1e4f3598df769818997f5232051a552aee08f02", 5696),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_i8.vmt", "97314e24b5fadf4a0506498340360c6b4c171ecb", 89),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_i8.vtf", "20a320ea2cb8a6f2acd3b40a31e9b8ae87b67fbf", 5669),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_ia88.vmt", "a636874f61926c6cadf6825f1034584896f95ca7", 91),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_ia88.vtf", "f4a34a268884afc78cfc5fbb9bcab268bf4a1672", 11130),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_rgb888.vmt", "018b409faac91692003095c0ad768db610e1a492", 93),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_rgb888.vtf", "6b1472d859093283f3b1fd81d55ffdec396d4127", 16591),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_rgba8888.vmt", "6c96842430ea3701fdc1c8614a89b77b9196ce5c", 95),
    ("source1/textures", "TeamSpen210/srctools", "tests/test_vtf/sample_rgba8888.vtf", "59630da785329d74d4afe90720d780689735dc98", 22052),
    ("source1/maps", "TeamSpen210/srctools", "tests/test_vec/rot_main.bsp", "7300fb737240a2524dbaaf948cdd91637a0b7d07", 824700),
    ("source1/maps", "ValveSoftware/source-sdk-2013", "game/mod_hl2mp/maps/dm_lockdown.bsp", "a6742a642c5dae791b97fbf91c9e9236b8fb5ef5", 11384400),
    ("source1/models", "LordVonAdel/source-mdl", "test/candles.dx90.vtx", "ac1c7c8bd3e4a58dd4a30e60fc4b827ebaa01caa", 5308),
    ("source1/models", "LordVonAdel/source-mdl", "test/candles.mdl", "aad37b511f372cd7594574e3179c2a86ab271ed0", 1728),
    ("source1/models", "LordVonAdel/source-mdl", "test/candles.phy", "9c5196ef1d4a55908c92d949d197586356f003da", 8941),
    ("source1/models", "LordVonAdel/source-mdl", "test/candles.vvd", "503541784a83f819f8e914b0a9f25fd8b8237a1d", 23808),
    ("source1/models", "icewind1991/vmdl", "data/barrel01.dx90.vtx", "5bd9c7d7a68b5ff5ced827de7d658e847bff0bbc", 14120),
    ("source1/models", "icewind1991/vmdl", "data/barrel01.mdl", "f8df506a348c5829c0d12c41952cc619da8b020f", 1724),
    ("source1/models", "icewind1991/vmdl", "data/barrel01.vvd", "3108f28926e879045b2dc0a634a7f6393ee5a7a0", 44736),
    ("goldsrc/models", "LogicAndTrick/sledge-formats", "Sledge.Formats.Model.Tests/Resources/mdl10/cube-tex.mdl", "5a3f3d3ace0b762a0593a1b08a9b46ea7276e36b", 5464),
    ("goldsrc/models", "LogicAndTrick/sledge-formats", "Sledge.Formats.Model.Tests/Resources/mdl10/cube-texT.mdl", "8c960e3d121aeecda27e63b82b1487a153854b65", 265792),
    ("goldsrc/models", "LogicAndTrick/sledge-formats", "Sledge.Formats.Model.Tests/Resources/mdl10/cube.mdl", "847d1ee7f1dc9b144eabaf6123531ff71b0e5869", 271012),
    ("goldsrc/maps", "LogicAndTrick/sledge-formats", "Sledge.Formats.Bsp.Tests/Resources/goldsource/aaa.bsp", "d7d19ec08c0b7fb7a03743d0040672b3a574a61c", 4152),
    ("goldsrc/maps", "EngineersBox/Lambda", "maps/test1.bsp", "bf248e873f7e8ff8578fdc6301702d6efd387c3e", 2276),
    ("goldsrc/maps", "EngineersBox/Lambda", "maps/test2.bsp", "479771520c65dd843ac2f790940eb7805a5fc64a", 27736),
    ("goldsrc/maps", "EngineersBox/Lambda", "maps/test3.bsp", "b33467dff51dedcc423df46f7e86d056116a3c12", 3436),
]


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parent.parent / "samples")
    parser.add_argument("--only", default="", help="comma separated top-level groups, e.g. source1,goldsrc")
    args = parser.parse_args()
    groups = {group for group in args.only.split(",") if group}

    failures = 0
    for folder, repo, repo_path, blob_sha, size in FILES:
        if groups and folder.split("/")[0] not in groups:
            continue
        target = args.dest / folder / Path(repo_path).name
        if target.is_file() and git_blob_sha1(target.read_bytes()) == blob_sha:
            continue
        url = f"https://raw.githubusercontent.com/{repo}/{COMMITS[repo]}/{repo_path}"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
        except OSError as ex:
            print(f"FAILED  {url}: {ex}")
            failures += 1
            continue
        if len(data) != size or git_blob_sha1(data) != blob_sha:
            print(f"FAILED  {url}: checksum mismatch")
            failures += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"fetched {folder}/{target.name} ({size} bytes)")

    print(f"done, {failures} failure(s); samples in {args.dest}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
