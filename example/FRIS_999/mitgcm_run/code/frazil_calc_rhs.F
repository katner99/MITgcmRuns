#include "FRAZIL_OPTIONS.h"

CBOP
C !ROUTINE: FRAZIL_CALC_RHS

C !INTERFACE: ==========================================================
      SUBROUTINE FRAZIL_CALC_RHS(
     I                     myTime, myIter, myThid )

C !DESCRIPTION:
C Check water temperature and if colder than freezing
C point bring excess negative heat to the surface.

C !USES: ===============================================================
      IMPLICIT NONE
#include "EEPARAMS.h"
#include "SIZE.h"
#include "PARAMS.h"
#include "GRID.h"
#include "DYNVARS.h"
#include "FFIELDS.h"
#ifdef ALLOW_FRAZIL
# include "FRAZIL.h"
#endif

C     !INPUT/OUTPUT PARAMETERS:
C     myTime :: current time in simulation
C     myIter :: current iteration number in simulation
C     myThid :: my Thread Id number
      _RL     myTime
      INTEGER myIter
      INTEGER myThid
CEOP

#ifdef ALLOW_FRAZIL

C     !LOCAL VARIABLES:
C     Tfreezing :: freezing threshold temperature
      INTEGER bi,bj,i,j,k,kTop
      _RL Tfreezing, Tresid, pLoc, sLoc, tLoc
      _RL a0, a1, a2, b, c0
      PARAMETER( a0 = -0.0575   _d  0 )
      PARAMETER( a1 = 0.0       _d  0 )
      PARAMETER( a2 = 0.0       _d  0 )
      PARAMETER( c0 = 0.0901    _d  0 )
      PARAMETER( b  = -7.61     _d -4 )
C      PARAMETER( a0 = -0.0575   _d  0 )
C      PARAMETER( a1 = 1.710523  _d -3 )
C      PARAMETER( a2 = -2.154996 _d -4 )
C      PARAMETER( b  = -7.53     _d -4 )

      _RL SW_TEMP
      EXTERNAL SW_TEMP

      DO bj=myByLo(myThid),myByHi(myThid)
       DO bi=myBxLo(myThid),myBxHi(myThid)

C     Initialize FrazilForcingT to zero
        DO k=1,Nr
         DO j=1-Oly,sNy+OLy
          DO i=1-Olx,sNx+Olx
           FrazilForcingT(i,j,k,bi,bj) = 0. _d 0
          ENDDO
         ENDDO
        ENDDO

C     Check for water below freezing point.
        DO k = 2, Nr
         DO j=1-OLy,sNy+OLy
          DO i=1-OLx,sNx+OLx
           IF ( maskC(i,j,k-1,bi,bj) .NE. 0. _d 0 .AND.
     &          maskC(i,j,k,  bi,bj) .NE. 0. _d 0 ) THEN
# ifdef FRAZIL_IGNORE_CAVITIES
C     Make sure we're not in a cavity (where maskC(k=1)=0)
           IF ( maskC(i,j,1,bi,bj) .NE. 0. _d 0) THEN
# else
           IF (.TRUE.) THEN
# endif

            sLoc = MAX(salt(i,j,k,bi,bj), 0. _d 0)
# ifdef FRAZIL_TFREEZE_SFC
C           tLoc is just the potential temperature
            tLoc = theta(i,j,k,bi,bj)
# else
C           tLoc is in-situ temperature; we also need pLoc
            pLoc = ABS(RC(k))
            tLoc = SW_TEMP(sLoc,theta(i,j,k,bi,bj),pLoc,0. _d 0)
# endif

C Freezing point of seawater
C   REFERENCE: UNESCO TECH. PAPERS IN THE MARINE SCIENCE NO. 28. 1978
C   EIGHTH REPORT JPOTS
C   ANNEX 6 FREEZING POINT OF SEAWATER F.J. MILLERO PP.29-35.
C
C  UNITS:
C         PRESSURE      P          DECIBARS
C         SALINITY      S          PSS-78
C         TEMPERATURE   TF         DEGREES CELSIUS
C         FREEZING PT.
C************************************************************
C  CHECKVALUE: TF= -2.588567 DEG. C FOR S=40.0, P=500. DECIBARS
# ifdef FRAZIL_TFREEZE_SFC
C           Surface freezing point
            Tfreezing = a0*sLoc + c0
# else
C           Depth-dependent freezing point
            Tfreezing = a0*sLoc + c0 + b*pLoc
# endif
C            Tfreezing = (a0 + a1*sqrt(sLoc) + a2*sLoc) * sLoc + b*pLoc

            IF (tLoc .LT. Tfreezing) THEN
C     Move the negative heat to surface level, or send it to space!
             kTop = kSurfC(i,j,bi,bj)
             Tresid = ( Tfreezing - tloc )
     &            * HeatCapacity_Cp * rUnit2mass
     &            * drF(k) * _hFacC(i,j,k,bi,bj)
             FrazilForcingT(i,j,k,bi,bj) = Tresid / dTtracerLev(k)
# ifndef FRAZIL_ZAP
             FrazilForcingT(i,j,kTop,bi,bj) =
     &            FrazilForcingT(i,j,kTop,bi,bj)
     &            - Tresid / dTtracerLev(kTop)
# endif
            ENDIF
           ENDIF
           ENDIF
          ENDDO
         ENDDO
        ENDDO
       ENDDO
      ENDDO

# ifdef ALLOW_DIAGNOSTICS
      IF ( useDiagnostics ) THEN
         CALL DIAGNOSTICS_FILL( FrazilForcingT, 'FrzForcT',
     &                          0,Nr, 0, 1, 1, myThid )
      ENDIF
# endif /* ALLOW_DIAGNOSTICS */

#endif /* ALLOW_FRAZIL */

      RETURN
      END
