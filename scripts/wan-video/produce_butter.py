"""OP341 four-shot creative production; reuses the bounded serial renderer."""
from pathlib import Path
import produce_short as renderer

renderer.OUT = Path('/home/dev/artifacts/crunchy-butter')
renderer.BUDGET_SECONDS = 1200
renderer.STYLE = (' Cheerful stylized 3D toy advertisement, pastel studio lighting, bright turquoise '
                  'craft mat, smooth clean shapes, one main object centered for a narrow vertical crop, '
                  'macro close-up, adult-sized lavender glove, simple physically coherent slow motion. '
                  'Clearly silicone craft toys, no food, no eating, no faces of people, no logos, no text.')
renderer.SHOTS = [
    ('squish', 'Can you make\nbutter crunchy?', 'One adult lavender-gloved hand slowly presses a thumb into a small rectangular bright yellow squishy silicone toy on a turquoise craft mat. The soft toy has two tiny black cartoon eyes and a smile on its front. The yellow toy slowly deforms and rebounds like a stress ball. Keep toy fully in the center.'),
    ('sprinkles', 'Only in the\nbutter laboratory!', 'A lavender-gloved adult hand gently pours a small spoonful of tiny bright pink and blue star-shaped foam craft pieces onto a rectangular yellow squishy silicone toy sitting in the center of a turquoise craft mat. The craft pieces tumble and bounce. Macro close-up, toy centered, no cooking equipment.'),
    ('crunch', 'That is a\ntiny rave!', 'A lavender-gloved adult hand slowly squeezes a rectangular yellow squishy silicone toy studded with tiny bright pink and blue star-shaped foam craft pieces. The soft yellow toy compresses between thumb and fingers and its colorful textured surface bunches up. Macro view, craft mat below, toy centered, satisfying slow squash.'),
    ('ta-da', 'BEHOLD!\nTOASTLESS TOAST.', 'One lavender-gloved adult hand presents a funny small rectangular bright yellow silicone squishy toy studded with pink and blue foam stars. The toy has two cute googly eyes, a little smile and a tiny blue party hat. The hand gently wobbles the toy in the center of the frame on a turquoise craft mat. Cheerful triumphant product reveal, simple clean background.'),
]

if __name__ == '__main__':
    renderer.main()
