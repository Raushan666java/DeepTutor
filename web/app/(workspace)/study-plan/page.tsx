import { Suspense } from 'react'
import StudyPlanPage from '@/features/study-plan/components/StudyPlanPage'

export default function Page() {
  return (
    <Suspense>
      <StudyPlanPage />
    </Suspense>
  )
}
