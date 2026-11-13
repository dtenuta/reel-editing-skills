import { Composition } from "remotion";
import { Reel } from "./Reel";
import edit from "./edit.json";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Reel"
      component={Reel}
      durationInFrames={edit.durationInFrames}
      fps={edit.fps}
      width={edit.width}
      height={edit.height}
    />
  );
};
