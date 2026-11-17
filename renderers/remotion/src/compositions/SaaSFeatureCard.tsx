import { Composition, interpolate, Style, Text, useCurrentFrame } from "remotion";

export interface SaaSFeatureCardProps {
  headline: string;
  subhead: string;
  accent: string;
}

const DURATION = 3;
const FPS = 60;
const WIDTH = 1080;
const HEIGHT = 1920;

export default function SaaSFeatureCard({
  headline,
  subhead,
  accent,
}: SaaSFeatureCardProps) {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, (DURATION - 1) * FPS], [0, 1]);
  const opacity = interpolate(progress, [0, 0.5, 1], [0, 0.3, 1]);

  return (
    <Composition
      id="SaaSFeatureCard"
      duration={DURATION}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      backgroundColor="#0B0F19"
    >
      <Style>
        {`
          .card {
            position: absolute;
            inset: 50%;
            transform: translate(-50%, -50%);
            width: 90vw;
            height: 60vh;
            background: #12182B;
            border: 1px solid ${accent};
            border-radius: 24px;
            opacity: ${opacity};
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #FFFFFF;
            font-family: system-ui, -apple-system, sans-serif;
          }
        `}
      </Style>
      <div className="card">
        <Text
          style={{
            fontSize: 40,
            fontWeight: "bold",
            textAlign: "center",
            lineHeight: 1.2,
          }}
        >
          {headline}
        </Text>
        <Text style={{ fontSize: 20, marginTop: 12, opacity: 0.9 }}>{subhead}</Text>
      </div>
    </Composition>
  );
}
